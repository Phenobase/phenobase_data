#!/usr/bin/env python3
"""Fetch Budburst observations and write one loader-ready Phenobase CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trait_lookup import canonical_label_for_urn, load_traits_catalog


TOKEN_URL = "https://budburst.org/api/sanctum/token"
OBSERVATIONS_URL = "https://budburst.org/api/observations"
OBSERVATION_METADATA_URL = "https://budburst.org/data/{report_id}"
TRAITS_CSV = REPO_ROOT / "data" / "traits.csv"
DEFAULT_MAPPINGS = BASE_DIR / "mappings.csv"

RAW_FIELDS = [
    "observation_id",
    "latitude",
    "longitude",
    "observation_date",
    "scientific_name",
    "phenophase_id",
    "plant_group_id",
    "add_date",
    "modified_date",
    "site_species_id",
    "report_id",
    "is_youth_observation",
]

OUTPUT_FIELDS = [
    "dataSource",
    "scientificName",
    "taxonRank",
    "basisOfRecord",
    "family",
    "genus",
    "annotationID",
    "date",
    "year",
    "dayOfYear",
    "latitude",
    "longitude",
    "observedMetadataUrl",
    "organismID",
    "occurrenceID",
    "annotation_method",
    "verbatimTrait",
    "phenophase_id",
    "plant_group_id",
    "report_id",
    "site_species_id",
    "trait_urn",
    "trait",
]


def norm(value):
    return "" if value is None else str(value).strip()


def truthy(value):
    return norm(value).lower() in {"1", "true", "yes", "y"}


def extract_genus(scientific_name):
    parts = norm(scientific_name).split()
    return parts[0] if parts else ""


def format_coordinate(value):
    return f"{float(norm(value)):.5f}"


def load_config(path):
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def request_text(url, *, params=None, headers=None, data=None, timeout=120, retries=3):
    full_url = url
    if params:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"

    body = None
    req_headers = dict(headers or {})
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    request = urllib.request.Request(
        full_url,
        data=body,
        headers={
            "Accept": "application/json",
            "User-Agent": "Phenobase-Budburst-Loader/1.0",
            **req_headers,
        },
    )
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            retryable = err.code == 429 or err.code >= 500
            if attempt >= retries or not retryable:
                raise RuntimeError(f"HTTP {err.code} from {full_url}: {detail[:1000]}") from err
            retry_after = err.headers.get("Retry-After")
        except Exception:
            if attempt >= retries:
                raise
            retry_after = None
        try:
            sleep_seconds = int(retry_after) if retry_after else min(60, 2 ** attempt)
        except Exception:
            sleep_seconds = min(60, 2 ** attempt)
        print(f"Request failed for {full_url}; retrying in {sleep_seconds}s ({attempt}/{retries})...", flush=True)
        time.sleep(sleep_seconds)
    raise RuntimeError(f"Request failed after {retries} attempts: {full_url}")


def request_json(url, *, params=None, headers=None, data=None, timeout=120, retries=3):
    return json.loads(request_text(url, params=params, headers=headers, data=data, timeout=timeout, retries=retries))


def token_from_config(config):
    token = os.environ.get("BUDBURST_TOKEN") or config.get("token")
    if token:
        return token

    email = os.environ.get("BUDBURST_EMAIL") or config.get("email")
    password = os.environ.get("BUDBURST_PASSWORD") or config.get("password")
    device_name = os.environ.get("BUDBURST_DEVICE_NAME") or config.get("device_name") or "phenobase_data"
    if not email or not password:
        raise RuntimeError(
            "Budburst auth is required. Provide BUDBURST_TOKEN, or email/password in config.json "
            "or BUDBURST_EMAIL/BUDBURST_PASSWORD."
        )

    raw_token = request_text(
        TOKEN_URL,
        data={"email": email, "password": password, "device_name": device_name},
        retries=3,
    )
    if not raw_token:
        raise RuntimeError("Empty Budburst auth response.")
    return raw_token.split("|", 1)[-1]


def load_mappings(path, traits_catalog):
    mappings = {}
    rows = 0
    missing_traits = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        if len(header) < 5:
            raise ValueError(f"Expected Budburst mapping file with duplicate PPO_ID columns: {path}")

        for raw in reader:
            rows += 1
            if len(raw) < 4:
                continue
            plant_group = norm(raw[0])
            phenophase = norm(raw[1])
            if not plant_group or not phenophase:
                continue

            trait_urns = []
            for idx in (2, 4):
                if len(raw) > idx:
                    trait_urn = norm(raw[idx])
                    if trait_urn:
                        trait_urns.append(trait_urn)

            records = []
            for trait_urn in trait_urns:
                trait = canonical_label_for_urn(trait_urn, traits_catalog)
                if not trait:
                    missing_traits += 1
                    continue
                records.append({"trait_urn": trait_urn, "trait": trait})

            if records:
                mappings[(plant_group, phenophase)] = records

    print(
        f"Loaded {rows} mapping row(s) across {len(mappings)} Budburst plant_group/phenophase pair(s) "
        f"from {path}. Missing PPO IDs in traits.csv: {missing_traits}",
        flush=True,
    )
    return mappings


def fetch_page(headers, per_page, page, created_after, timeout, retries):
    params = {
        "report_type": "phenophase",
        "per_page": per_page,
        "page": page,
    }
    if created_after:
        params["created_after"] = created_after

    data = request_json(OBSERVATIONS_URL, params=params, headers=headers, timeout=timeout, retries=retries)
    meta = data.get("meta") or {}
    last_page = int(meta.get("last_page") or page)
    observations = data.get("data") or []
    return page, last_page, observations


def observation_pages(headers, per_page, start_page, max_pages, created_after, timeout, retries, workers):
    first_page, last_page, observations = fetch_page(headers, per_page, start_page, created_after, timeout, retries)
    yield first_page, last_page, observations

    end_page = last_page
    if max_pages is not None:
        end_page = min(last_page, start_page + max_pages - 1)
    if first_page >= end_page:
        return

    page_numbers = range(first_page + 1, end_page + 1)
    if workers <= 1:
        for page in page_numbers:
            yield fetch_page(headers, per_page, page, created_after, timeout, retries)
        return

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_page, headers, per_page, page, created_after, timeout, retries): page
            for page in page_numbers
        }
        for future in as_completed(futures):
            yield future.result()


def parse_observation_date(value):
    value = norm(value)
    if not value:
        return None
    return date.fromisoformat(value)


def transform_observation(row, mappings, counters, include_youth):
    counters["rawRows"] += 1

    if not include_youth and truthy(row.get("is_youth_observation")):
        counters["droppedYouth"] += 1
        return

    scientific_name = norm(row.get("scientific_name"))
    if not scientific_name:
        counters["droppedMissingScientificName"] += 1
        return

    try:
        obs_date = parse_observation_date(row.get("observation_date"))
    except Exception:
        obs_date = None
    if obs_date is None:
        counters["droppedBadDate"] += 1
        return

    try:
        latitude = format_coordinate(row.get("latitude"))
        longitude = format_coordinate(row.get("longitude"))
    except Exception:
        counters["droppedMissingCoords"] += 1
        return

    plant_group = norm(row.get("plant_group_id"))
    phenophase = norm(row.get("phenophase_id"))
    trait_records = mappings.get((plant_group, phenophase))
    if not trait_records:
        counters["droppedNoMap"] += 1
        return

    observation_id = norm(row.get("observation_id"))
    site_species_id = norm(row.get("site_species_id"))
    report_id = norm(row.get("report_id"))
    occurrence_id = f"budburst:{observation_id}" if observation_id else ""
    organism_id = f"budburst:site_species:{site_species_id}" if site_species_id else ""
    verbatim_trait = f"plantGroup = {plant_group}; phenophase = {phenophase}"

    for idx, trait_record in enumerate(trait_records, start=1):
        trait_urn = trait_record["trait_urn"]
        trait = trait_record["trait"]
        annotation_id = f"budburst:{observation_id}:{plant_group}:{phenophase}:{idx}:{trait_urn.replace(':', '_')}"
        counters["keptCount"] += 1

        yield OrderedDict(
            [
                ("dataSource", "Budburst"),
                ("scientificName", scientific_name),
                ("taxonRank", "species"),
                ("basisOfRecord", "Human Observation"),
                ("family", ""),
                ("genus", extract_genus(scientific_name)),
                ("annotationID", annotation_id),
                ("date", obs_date.isoformat()),
                ("year", obs_date.year),
                ("dayOfYear", obs_date.timetuple().tm_yday),
                ("latitude", latitude),
                ("longitude", longitude),
                (
                    "observedMetadataUrl",
                    OBSERVATION_METADATA_URL.format(report_id=report_id or observation_id)
                    if report_id or observation_id
                    else "",
                ),
                ("organismID", organism_id),
                ("occurrenceID", occurrence_id),
                ("annotation_method", "human"),
                ("verbatimTrait", verbatim_trait),
                ("phenophase_id", phenophase),
                ("plant_group_id", plant_group),
                ("report_id", report_id),
                ("site_species_id", site_species_id),
                ("trait_urn", trait_urn),
                ("trait", trait),
            ]
        )


def empty_counters():
    return {
        "rawRows": 0,
        "keptCount": 0,
        "droppedYouth": 0,
        "droppedMissingScientificName": 0,
        "droppedBadDate": 0,
        "droppedMissingCoords": 0,
        "droppedNoMap": 0,
    }


def print_counters(counters):
    print(
        "Raw rows: {rawRows} | Kept rows: {keptCount} | Dropped youth: {droppedYouth} | "
        "Dropped missing scientific name: {droppedMissingScientificName} | Dropped bad date: {droppedBadDate} | "
        "Dropped missing coords: {droppedMissingCoords} | Dropped no map: {droppedNoMap}".format(**counters),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Fetch Budburst and write one loader-ready CSV.")
    parser.add_argument("--output", default="budburst_observations.csv", help="Loader-ready output CSV path. Defaults to current directory.")
    parser.add_argument("--raw-output", default=None, help="Optional raw API CSV path to write at the same time.")
    parser.add_argument("--from-raw", default=None, help="Skip API fetch and map an existing raw Budburst CSV.")
    parser.add_argument("--mappings", default=str(DEFAULT_MAPPINGS), help="Budburst PPO mapping CSV.")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"), help="JSON config path for auth.")
    parser.add_argument("--per-page", type=int, default=1000, help="Rows per page. Budburst max is 1000.")
    parser.add_argument("--start-page", type=int, default=1, help="First API page to fetch.")
    parser.add_argument("--max-pages", type=int, default=None, help="Stop after this many pages for testing.")
    parser.add_argument("--created-after", default=None, help="Optional created_after API filter, e.g. 2023-12-01.")
    parser.add_argument("--include-youth", action="store_true", help="Include youth observations. Default is to exclude them.")
    parser.add_argument("--timeout", type=int, default=300, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Retries per API request.")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel page fetch workers.")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    traits_catalog = load_traits_catalog(str(TRAITS_CSV))
    mappings = load_mappings(Path(args.mappings), traits_catalog)
    counters = empty_counters()

    with output_path.open("w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()

        if args.from_raw:
            with Path(args.from_raw).open(newline="", encoding="utf-8-sig") as raw_f:
                reader = csv.DictReader(raw_f)
                for observation in reader:
                    for transformed in transform_observation(observation, mappings, counters, args.include_youth):
                        writer.writerow(transformed)
        else:
            config = load_config(Path(args.config))
            token = token_from_config(config)
            headers = {"Authorization": f"Bearer {token}"}

            raw_writer = None
            raw_f = None
            if args.raw_output:
                raw_path = Path(args.raw_output)
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_f = raw_path.open("w", newline="", encoding="utf-8")
                raw_writer = csv.DictWriter(raw_f, fieldnames=RAW_FIELDS, extrasaction="ignore")
                raw_writer.writeheader()

            try:
                for page, last_page, observations in observation_pages(
                    headers,
                    args.per_page,
                    args.start_page,
                    args.max_pages,
                    args.created_after,
                    args.timeout,
                    args.retries,
                    args.workers,
                ):
                    page_kept = 0
                    for observation in observations:
                        if raw_writer:
                            raw_writer.writerow(observation)
                        before = counters["keptCount"]
                        for transformed in transform_observation(observation, mappings, counters, args.include_youth):
                            writer.writerow(transformed)
                        page_kept += counters["keptCount"] - before
                    print(
                        f"Fetched page {page} of {last_page}: {len(observations)} raw row(s), {page_kept} mapped row(s)",
                        flush=True,
                    )
            finally:
                if raw_f:
                    raw_f.close()

    print(f"Wrote {counters['keptCount']} loader-ready row(s) to {output_path}", flush=True)
    print_counters(counters)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
