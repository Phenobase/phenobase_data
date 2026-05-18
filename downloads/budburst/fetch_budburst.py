#!/usr/bin/env python3
"""Fetch Budburst phenophase observations to CSV."""

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TOKEN_URL = "https://budburst.org/api/sanctum/token"
OBSERVATIONS_URL = "https://budburst.org/api/observations"

FIELDNAMES = [
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


def load_config(path):
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def request_json(url, *, params=None, headers=None, data=None, timeout=120, retries=3):
    text = request_text(url, params=params, headers=headers, data=data, timeout=timeout, retries=retries)
    return json.loads(text)


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
            if attempt >= retries or err.code < 500:
                raise RuntimeError(f"HTTP {err.code} from {full_url}: {detail[:1000]}") from err
        except Exception:
            if attempt >= retries:
                raise
        sleep_seconds = min(30, 2 ** attempt)
        print(f"Request failed for {full_url}; retrying in {sleep_seconds}s ({attempt}/{retries})...", flush=True)
        time.sleep(sleep_seconds)
    raise RuntimeError(f"Request failed after {retries} attempts: {full_url}")


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


def yield_page(page, last_page, observations, exclude_youth):
    yielded = 0
    for observation in observations:
        if exclude_youth and str(observation.get("is_youth_observation")).strip().lower() in {"1", "true", "yes"}:
            continue
        yielded += 1
        yield observation
    print(f"Fetched page {page} of {last_page}: {len(observations)} raw row(s), {yielded} kept row(s)", flush=True)


def observation_rows(headers, per_page, start_page, max_pages, created_after, exclude_youth, timeout, retries):
    page = start_page
    pages_seen = 0

    while True:
        page, last_page, observations = fetch_page(headers, per_page, page, created_after, timeout, retries)

        for observation in yield_page(page, last_page, observations, exclude_youth):
            yield page, last_page, observation

        pages_seen += 1
        if page >= last_page:
            break
        if max_pages is not None and pages_seen >= max_pages:
            break
        page += 1


def parallel_observation_rows(headers, per_page, start_page, max_pages, created_after, exclude_youth, timeout, retries, workers):
    first_page, last_page, observations = fetch_page(headers, per_page, start_page, created_after, timeout, retries)
    for observation in yield_page(first_page, last_page, observations, exclude_youth):
        yield first_page, last_page, observation

    end_page = last_page
    if max_pages is not None:
        end_page = min(last_page, start_page + max_pages - 1)
    if first_page >= end_page:
        return

    page_numbers = range(first_page + 1, end_page + 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_page, headers, per_page, page, created_after, timeout, retries): page
            for page in page_numbers
        }
        for future in as_completed(futures):
            page, page_last_page, page_observations = future.result()
            for observation in yield_page(page, page_last_page, page_observations, exclude_youth):
                yield page, page_last_page, observation


def main():
    parser = argparse.ArgumentParser(description="Fetch Budburst phenophase observations to CSV.")
    parser.add_argument("--output", default="budburst.csv", help="Output CSV path.")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"), help="JSON config path for auth.")
    parser.add_argument("--per-page", type=int, default=1000, help="Rows per page. Budburst max is 1000.")
    parser.add_argument("--start-page", type=int, default=1, help="First API page to fetch.")
    parser.add_argument("--max-pages", type=int, default=None, help="Stop after this many pages for testing.")
    parser.add_argument("--created-after", default=None, help="Optional created_after API filter, e.g. 2023-12-01.")
    parser.add_argument("--exclude-youth", action="store_true", help="Skip youth observations in the written CSV.")
    parser.add_argument("--append", action="store_true", help="Append rows to an existing output file instead of overwriting it.")
    parser.add_argument("--timeout", type=int, default=300, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=5, help="Retries per API request.")
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel page fetch workers.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    token = token_from_config(config)
    headers = {"Authorization": f"Bearer {token}"}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    row_source = parallel_observation_rows if args.workers > 1 else observation_rows

    write_header = not args.append or not output_path.exists() or output_path.stat().st_size == 0
    mode = "a" if args.append else "w"
    written = 0
    with output_path.open(mode, newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        row_iter = row_source(
            headers,
            args.per_page,
            args.start_page,
            args.max_pages,
            args.created_after,
            args.exclude_youth,
            args.timeout,
            args.retries,
            args.workers,
        ) if args.workers > 1 else row_source(
            headers,
            args.per_page,
            args.start_page,
            args.max_pages,
            args.created_after,
            args.exclude_youth,
            args.timeout,
            args.retries,
        )
        for _, _, observation in row_iter:
            writer.writerow(observation)
            written += 1

    verb = "Appended" if args.append else "Wrote"
    print(f"{verb} {written} row(s) to {output_path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
