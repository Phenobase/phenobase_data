#!/usr/bin/env python3
"""Fetch SeasonWatch India DwC-A and write one loader-ready Phenobase CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trait_lookup import canonical_label_for_urn, load_traits_catalog


ARCHIVE_URL = "https://cloud.gbif.org/asia/archive.do?r=seasonwatch-ncfindia&v=1.6"
GBIF_DATASET_KEY = "d85d848d-791e-4055-bda5-32c16619ab21"
GBIF_OCCURRENCE_API_URL = "https://api.gbif.org/v1/occurrence/search"
GBIF_OCCURRENCE_URL = "https://www.gbif.org/occurrence/{gbif_key}"
GBIF_OCCURRENCE_SEARCH_URL = "https://www.gbif.org/occurrence/search"
DEFAULT_ARCHIVE = BASE_DIR / "seasonwatch-ncfindia.dwca.zip"
DEFAULT_GBIF_KEY_CACHE = BASE_DIR / "seasonwatchindia_gbif_keys.csv"
DEFAULT_MAPPINGS = BASE_DIR / "mappings.csv"
TRAITS_CSV = REPO_ROOT / "data" / "traits.csv"

VALUE_MAP = {
    "none": "0",
    "few": "1",
    "many": "2",
}

OUTPUT_FIELDS = [
    "dataSource",
    "scientificName",
    "taxonRank",
    "collectionMethod",
    "family",
    "genus",
    "species",
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
    "recordedBy",
    "verbatimTrait",
    "measurementType",
    "measurementValue",
    "trait_urn",
    "trait",
]


def norm(value):
    return "" if value is None else str(value).strip()


def normalize_key(value):
    return re.sub(r"[^a-z0-9]+", "", norm(value).lower())


def format_coordinate(value):
    return f"{float(norm(value)):.5f}"


def gbif_occurrence_search_url(occurrence_id):
    query = urllib.parse.urlencode(
        {
            "dataset_key": GBIF_DATASET_KEY,
            "occurrence_id": occurrence_id,
        }
    )
    return f"{GBIF_OCCURRENCE_SEARCH_URL}?{query}"


def observed_metadata_url(occurrence_id, gbif_key_by_occurrence_id=None):
    if not occurrence_id:
        return ""
    gbif_key = (gbif_key_by_occurrence_id or {}).get(occurrence_id)
    if gbif_key:
        return GBIF_OCCURRENCE_URL.format(gbif_key=gbif_key)
    return gbif_occurrence_search_url(occurrence_id)


def request_download(url, output_path, timeout=300, retries=5):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Phenobase-SeasonWatch-Loader/1.0"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response, output_path.open("wb") as out_f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out_f.write(chunk)
            return
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            retryable = err.code == 429 or err.code >= 500
            if attempt >= retries or not retryable:
                raise RuntimeError(f"HTTP {err.code} from {url}: {detail[:1000]}") from err
        except Exception:
            if attempt >= retries:
                raise
        sleep_seconds = min(60, 2 ** attempt)
        print(f"Archive download failed; retrying in {sleep_seconds}s ({attempt}/{retries})...", flush=True)
        time.sleep(sleep_seconds)


def request_json(url, params, timeout=120, retries=5):
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Phenobase-SeasonWatch-Loader/1.0",
        },
    )
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
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
        print(f"GBIF request failed; retrying in {sleep_seconds}s ({attempt}/{retries})...", flush=True)
        time.sleep(sleep_seconds)
    raise RuntimeError(f"Request failed after {retries} attempts: {full_url}")


def load_gbif_key_cache(path):
    gbif_keys = {}
    if not path or not path.exists():
        return gbif_keys
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            occurrence_id = norm(row.get("occurrenceID"))
            gbif_key = norm(row.get("gbifKey"))
            if occurrence_id and gbif_key:
                gbif_keys[occurrence_id] = gbif_key
    return gbif_keys


def write_gbif_key_cache(path, gbif_keys):
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["occurrenceID", "gbifKey"])
        writer.writeheader()
        for occurrence_id in sorted(gbif_keys):
            writer.writerow({"occurrenceID": occurrence_id, "gbifKey": gbif_keys[occurrence_id]})


def resolve_gbif_keys(occurrence_ids, cache_path, batch_size, timeout, retries):
    occurrence_ids = sorted({norm(v) for v in occurrence_ids if norm(v)})
    gbif_keys = load_gbif_key_cache(cache_path)
    missing = [occurrence_id for occurrence_id in occurrence_ids if occurrence_id not in gbif_keys]

    print(
        f"Loaded {len(gbif_keys)} cached GBIF key(s); resolving {len(missing)} missing "
        f"SeasonWatch occurrence key(s).",
        flush=True,
    )
    if not missing:
        return gbif_keys

    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        params = [
            ("datasetKey", GBIF_DATASET_KEY),
            ("limit", len(batch)),
            *[("occurrenceId", occurrence_id) for occurrence_id in batch],
        ]
        data = request_json(GBIF_OCCURRENCE_API_URL, params, timeout=timeout, retries=retries)
        for record in data.get("results") or []:
            occurrence_id = norm(record.get("occurrenceID") or record.get("identifier"))
            gbif_key = norm(record.get("key") or record.get("gbifID"))
            if occurrence_id and gbif_key:
                gbif_keys[occurrence_id] = gbif_key

        if (start + len(batch)) % max(batch_size * 50, 1) == 0 or start + len(batch) >= len(missing):
            print(
                f"Resolved {min(start + len(batch), len(missing))}/{len(missing)} missing GBIF key(s); "
                f"cache size={len(gbif_keys)}",
                flush=True,
            )
            write_gbif_key_cache(cache_path, gbif_keys)

    write_gbif_key_cache(cache_path, gbif_keys)
    return gbif_keys


def load_mappings(path, traits_catalog):
    mappings = {}
    rows = 0
    missing_traits = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if not any(norm(v) for v in raw.values()):
                continue
            rows += 1
            phenophase = normalize_key(raw.get("Phenophases"))
            value = norm(raw.get("Value"))
            trait_urn = norm(raw.get("PPO_ID"))
            if not phenophase or value == "" or not trait_urn:
                continue
            trait = canonical_label_for_urn(trait_urn, traits_catalog)
            if not trait:
                missing_traits += 1
                continue
            mappings[(phenophase, value)] = {"trait_urn": trait_urn, "trait": trait}

    print(
        f"Loaded {rows} mapping row(s) across {len(mappings)} SeasonWatch phenophase/value pair(s) "
        f"from {path}. Missing PPO IDs in traits.csv: {missing_traits}",
        flush=True,
    )
    return mappings


def parse_event_date(value):
    value = norm(value)
    if not value:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported eventDate format: {value}")


def load_occurrences(zf):
    occurrences = {}
    with zf.open("occurrence.txt") as raw_f:
        text = (line.decode("utf-8") for line in raw_f)
        reader = csv.DictReader(text, delimiter="\t")
        for row in reader:
            occurrence_id = norm(row.get("id"))
            if occurrence_id:
                occurrences[occurrence_id] = row
    return occurrences


def transform_measurement(occurrence, measurement, mapping_record, counters, gbif_key_by_occurrence_id=None):
    try:
        obs_date = parse_event_date(occurrence.get("eventDate"))
    except Exception:
        counters["droppedBadDate"] += 1
        return
    if obs_date is None:
        counters["droppedBadDate"] += 1
        return

    try:
        latitude = format_coordinate(occurrence.get("decimalLatitude"))
        longitude = format_coordinate(occurrence.get("decimalLongitude"))
    except Exception:
        counters["droppedMissingCoords"] += 1
        return

    scientific_name = norm(occurrence.get("scientificName"))
    if not scientific_name:
        counters["droppedMissingScientificName"] += 1
        return

    occurrence_id = norm(occurrence.get("occurrenceID") or occurrence.get("id"))
    organism_id = norm(occurrence.get("organismID"))
    measurement_type = norm(measurement.get("measurementType"))
    measurement_value = norm(measurement.get("measurementValue"))
    trait_urn = mapping_record["trait_urn"]
    trait = mapping_record["trait"]
    annotation_id = f"{occurrence_id}:{measurement_type}:{measurement_value}:{trait_urn.replace(':', '_')}"

    counters["keptCount"] += 1
    yield OrderedDict(
        [
            ("dataSource", "SeasonWatch (India)"),
            ("scientificName", scientific_name),
            ("taxonRank", norm(occurrence.get("taxonRank")) or "species"),
            ("collectionMethod", "human observation"),
            ("family", ""),
            ("genus", norm(occurrence.get("genus"))),
            ("species", norm(occurrence.get("specificEpithet"))),
            ("annotationID", annotation_id),
            ("date", obs_date.isoformat()),
            ("year", obs_date.year),
            ("dayOfYear", obs_date.timetuple().tm_yday),
            ("latitude", latitude),
            ("longitude", longitude),
            (
                "observedMetadataUrl",
                observed_metadata_url(occurrence_id, gbif_key_by_occurrence_id),
            ),
            ("organismID", organism_id),
            ("occurrenceID", occurrence_id),
            ("annotation_method", "human"),
            ("recordedBy", norm(occurrence.get("recordedByID"))),
            ("verbatimTrait", f"{measurement_type} = {measurement_value}"),
            ("measurementType", measurement_type),
            ("measurementValue", measurement_value),
            ("trait_urn", trait_urn),
            ("trait", trait),
        ]
    )


def empty_counters():
    return {
        "rawMeasurements": 0,
        "keptCount": 0,
        "droppedUnmappedValue": 0,
        "droppedNoMap": 0,
        "droppedMissingOccurrence": 0,
        "droppedMissingScientificName": 0,
        "droppedBadDate": 0,
        "droppedMissingCoords": 0,
    }


def print_counters(counters):
    print(
        "Raw measurements: {rawMeasurements} | Kept rows: {keptCount} | "
        "Dropped unmapped value: {droppedUnmappedValue} | Dropped no map: {droppedNoMap} | "
        "Dropped missing occurrence: {droppedMissingOccurrence} | Dropped missing scientific name: {droppedMissingScientificName} | "
        "Dropped bad date: {droppedBadDate} | Dropped missing coords: {droppedMissingCoords}".format(**counters),
        flush=True,
    )


def write_loader_csv(
    archive_path,
    output_path,
    mappings,
    max_measurements=None,
    gbif_direct_links=True,
    gbif_key_cache=None,
    gbif_batch_size=100,
    timeout=120,
    retries=5,
):
    counters = empty_counters()
    value_counts = Counter()

    with zipfile.ZipFile(archive_path) as zf:
        occurrences = load_occurrences(zf)
        print(f"Loaded {len(occurrences)} occurrence row(s) from {archive_path}", flush=True)
        gbif_key_by_occurrence_id = {}
        if gbif_direct_links:
            gbif_key_by_occurrence_id = resolve_gbif_keys(
                (row.get("occurrenceID") or row.get("id") for row in occurrences.values()),
                gbif_key_cache,
                gbif_batch_size,
                timeout,
                retries,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()

            with zf.open("measurementorfacts.txt") as raw_f:
                text = (line.decode("utf-8") for line in raw_f)
                reader = csv.DictReader(text, delimiter="\t")
                for measurement in reader:
                    if max_measurements and counters["rawMeasurements"] >= max_measurements:
                        break
                    counters["rawMeasurements"] += 1

                    measurement_value = norm(measurement.get("measurementValue"))
                    value_counts[measurement_value] += 1
                    mapped_value = VALUE_MAP.get(measurement_value.lower())
                    if mapped_value is None:
                        counters["droppedUnmappedValue"] += 1
                        continue

                    mapping_key = (normalize_key(measurement.get("measurementType")), mapped_value)
                    mapping_record = mappings.get(mapping_key)
                    if not mapping_record:
                        counters["droppedNoMap"] += 1
                        continue

                    occurrence = occurrences.get(norm(measurement.get("id")))
                    if not occurrence:
                        counters["droppedMissingOccurrence"] += 1
                        continue

                    for transformed in transform_measurement(
                        occurrence,
                        measurement,
                        mapping_record,
                        counters,
                        gbif_key_by_occurrence_id,
                    ):
                        writer.writerow(transformed)

                    if counters["rawMeasurements"] % 500000 == 0:
                        print_counters(counters)

    print(f"Wrote {counters['keptCount']} loader-ready row(s) to {output_path}", flush=True)
    print_counters(counters)
    print(f"Measurement values: {dict(value_counts)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fetch SeasonWatch India DwC-A and write one loader-ready CSV.")
    parser.add_argument("--output", default="seasonwatchindia_observations.csv", help="Loader-ready output CSV path. Defaults to current directory.")
    parser.add_argument("--archive", default=str(DEFAULT_ARCHIVE), help="Local DwC-A zip path.")
    parser.add_argument("--mappings", default=str(DEFAULT_MAPPINGS), help="SeasonWatch PPO mapping CSV.")
    parser.add_argument("--download", action="store_true", help="Download or refresh the DwC-A archive before processing.")
    parser.add_argument("--archive-url", default=ARCHIVE_URL, help="DwC-A archive URL.")
    parser.add_argument("--max-measurements", type=int, default=None, help="Limit measurement rows for testing.")
    parser.add_argument(
        "--gbif-direct-links",
        dest="gbif_direct_links",
        action="store_true",
        help="Resolve GBIF occurrence keys and write direct GBIF occurrence URLs. This is the default.",
    )
    parser.add_argument(
        "--no-gbif-direct-links",
        dest="gbif_direct_links",
        action="store_false",
        help="Skip GBIF key resolution and write GBIF occurrence search URLs instead.",
    )
    parser.add_argument("--gbif-key-cache", default=str(DEFAULT_GBIF_KEY_CACHE), help="CSV cache for occurrenceID to GBIF key lookups.")
    parser.add_argument("--gbif-batch-size", type=int, default=100, help="Number of occurrenceID filters per GBIF API request.")
    parser.add_argument("--timeout", type=int, default=300, help="Archive download timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Archive download retries.")
    parser.set_defaults(gbif_direct_links=True)
    args = parser.parse_args()

    archive_path = Path(args.archive)
    if args.download or not archive_path.exists():
        print(f"Downloading SeasonWatch DwC-A to {archive_path}", flush=True)
        request_download(args.archive_url, archive_path, timeout=args.timeout, retries=args.retries)

    traits_catalog = load_traits_catalog(str(TRAITS_CSV))
    mappings = load_mappings(Path(args.mappings), traits_catalog)
    write_loader_csv(
        archive_path,
        Path(args.output),
        mappings,
        max_measurements=args.max_measurements,
        gbif_direct_links=args.gbif_direct_links,
        gbif_key_cache=Path(args.gbif_key_cache),
        gbif_batch_size=args.gbif_batch_size,
        timeout=args.timeout,
        retries=args.retries,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
