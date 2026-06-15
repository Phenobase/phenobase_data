#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from export_schema import load_export_field_order, project_export_record


DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DEFAULT_INDEX = "phenobase2"
DEFAULT_QUERY = "*"
DEFAULT_BATCH_SIZE = 5000
DEFAULT_SCROLL = "2m"
DEFAULT_LIMIT = 0
DEFAULT_OUTPUT = "downloads/phenobase_dump.csv"
DEFAULT_COLUMNS_PATH = "data/columns.csv"
DEFAULT_REQUEST_TIMEOUT = 60
NPN_OBSERVATION_METADATA_URL = (
    "https://services.usanpn.org/npn_portal/observations/getObservationById.json"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export a Phenobase CSV dump by scrolling through the public query API."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Phenobase query API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--index",
        default=DEFAULT_INDEX,
        help=f"Elasticsearch index name to query (default: {DEFAULT_INDEX})",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"Lucene query string for the export (default: {DEFAULT_QUERY})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Hits to request per scroll page (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--scroll",
        default=DEFAULT_SCROLL,
        help=f"Scroll keepalive to request from Elasticsearch (default: {DEFAULT_SCROLL})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Maximum number of rows to export. Use 0 for no client-side limit (default: 0).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Destination CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--columns-path",
        default=DEFAULT_COLUMNS_PATH,
        help=f"Schema file used for CSV column order (default: {DEFAULT_COLUMNS_PATH})",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=f"Per-request timeout in seconds for API calls (default: {DEFAULT_REQUEST_TIMEOUT})",
    )
    return parser.parse_args()


def normalize_base_url(base_url):
    return base_url.rstrip("/")


def build_es_url(base_url, path, params=None):
    params = params or {}
    query_string = urllib.parse.urlencode(params)
    suffix = f"?{query_string}" if query_string else ""
    # The Phenobase proxy strips `/phenobase/api/v1/query/` literally and then
    # concatenates the remainder onto the ES host without inserting a slash.
    # We therefore intentionally send `.../query//phenobase2/_search`.
    return f"{normalize_base_url(base_url)}//{path.lstrip('/')}{suffix}"


def ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_field_order(columns_path):
    return load_export_field_order(columns_path)


def build_initial_body(query, batch_size):
    if query.strip() in {"", "*"}:
        query_body = {"match_all": {}}
    else:
        query_body = {
            "query_string": {
                "query": query,
                "analyze_wildcard": True,
            }
        }

    return {
        "size": batch_size,
        "sort": ["_doc"],
        "query": query_body,
    }


def post_json(url, payload, timeout_seconds):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


def get_total_hits(response_body):
    total = ((response_body or {}).get("hits") or {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value", 0))
    return int(total or 0)


def serialize_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return "|".join("" if item is None else str(item) for item in value)
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return value


def derive_observed_metadata_url(record):
    existing_url = str(
        (record or {}).get("sourceRecordUrl")
        or (record or {}).get("observedMetadataUrl")
        or ""
    ).strip()
    if existing_url:
        return existing_url

    raw_id = str((record or {}).get("annotationID") or "").strip()
    data_source = str((record or {}).get("dataSource") or "")
    npn_id = ""

    if raw_id.startswith("npn:"):
        npn_id = raw_id[4:]
    elif re.fullmatch(r"\d+", raw_id) and re.search(r"national phenology network", data_source, re.IGNORECASE):
        npn_id = raw_id

    if not npn_id:
        return ""

    query = urllib.parse.urlencode(
        {
            "request_src": "PPO",
            "observation_id": npn_id,
            "pretty": "1",
        }
    )
    return f"{NPN_OBSERVATION_METADATA_URL}?{query}"


def enrich_download_record(record):
    enriched = dict(record or {})
    if not str(enriched.get("sourceRecordUrl") or enriched.get("observedMetadataUrl") or "").strip():
        derived_url = derive_observed_metadata_url(enriched)
        if derived_url:
            enriched["observedMetadataUrl"] = derived_url
    return enriched


def build_csv_row(source, field_order):
    projected = project_export_record(enrich_download_record(source), field_order)
    return {field: serialize_value(projected.get(field)) for field in field_order}


def fetch_initial_page(base_url, index_name, query, batch_size, scroll, timeout_seconds):
    url = build_es_url(base_url, f"{index_name}/_search", {"scroll": scroll})
    return post_json(url, build_initial_body(query, batch_size), timeout_seconds)


def fetch_scroll_page(base_url, scroll_id, scroll, timeout_seconds):
    url = build_es_url(base_url, "_search/scroll")
    return post_json(url, {"scroll": scroll, "scroll_id": scroll_id}, timeout_seconds)


def print_page_progress(page_number, page_hits, total_written, expected_total, page_elapsed):
    if expected_total:
        print(
            f"Page {page_number}: fetched {page_hits:,} rows in {page_elapsed:.1f}s; "
            f"wrote {total_written:,}/{expected_total:,} total rows."
        )
    else:
        print(
            f"Page {page_number}: fetched {page_hits:,} rows in {page_elapsed:.1f}s; "
            f"wrote {total_written:,} total rows."
        )


def export_rows(args):
    field_order = load_field_order(args.columns_path)
    if not field_order:
        raise RuntimeError(f"No fields found in {args.columns_path}")

    ensure_parent_dir(args.output)

    total_written = 0
    page_number = 0
    response = fetch_initial_page(
        args.base_url,
        args.index,
        args.query,
        args.batch_size,
        args.scroll,
        args.request_timeout,
    )
    expected_total = get_total_hits(response)
    scroll_id = response.get("_scroll_id")

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()

        while True:
            page_number += 1
            page_started_at = time.monotonic()
            hits = ((response or {}).get("hits") or {}).get("hits") or []
            if not hits:
                print(f"Page {page_number}: fetched 0 rows; export complete.")
                break

            for hit in hits:
                if args.limit > 0 and total_written >= args.limit:
                    print(f"Reached client-side limit of {args.limit:,} rows.")
                    return total_written, expected_total
                writer.writerow(build_csv_row(hit.get("_source") or {}, field_order))
                total_written += 1
            fh.flush()
            page_elapsed = time.monotonic() - page_started_at
            print_page_progress(page_number, len(hits), total_written, expected_total, page_elapsed)

            if args.limit > 0 and total_written >= args.limit:
                break
            if not scroll_id:
                break

            response = fetch_scroll_page(args.base_url, scroll_id, args.scroll, args.request_timeout)
            scroll_id = response.get("_scroll_id")

    return total_written, expected_total


def main():
    args = parse_args()
    if args.batch_size <= 0:
        print("--batch-size must be greater than 0.", file=sys.stderr)
        return 1
    if args.limit < 0:
        print("--limit must be 0 or greater.", file=sys.stderr)
        return 1
    if args.request_timeout <= 0:
        print("--request-timeout must be greater than 0.", file=sys.stderr)
        return 1

    try:
        written, expected_total = export_rows(args)
    except FileNotFoundError as exc:
        print(f"Missing file: {exc.filename}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1
    except socket.timeout:
        print(
            f"Network timeout: the API did not respond within {args.request_timeout} seconds.",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Saved CSV dump to {os.path.abspath(args.output)}")
    if expected_total:
        print(f"Rows written: {written:,} of {expected_total:,} reported by the API.")
    else:
        print(f"Rows written: {written:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
