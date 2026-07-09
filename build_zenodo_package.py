#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build a Zenodo-ready Phenobase data package.

The package contains a compressed single-table CSV export, data dictionary files
derived from data/columns.csv, source counts, checksums, and a Zenodo metadata
template. The export uses the public Phenobase query API, matching
download_csv_dump.py.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import socket
import sys
import time
import zipfile
from collections import Counter, OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
import urllib.error

from download_csv_dump import (
    DEFAULT_BASE_URL,
    DEFAULT_BATCH_SIZE,
    DEFAULT_INDEX,
    DEFAULT_QUERY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCROLL,
    build_es_url,
    build_csv_row,
    fetch_initial_page,
    fetch_scroll_page,
    get_total_hits,
    post_json,
    should_skip_source,
)


DEFAULT_COLUMNS_PATH = "data/columns.csv"
DEFAULT_OUTPUT_DIR = "downloads/zenodo"
DEFAULT_TITLE = "Phenobase Plant Phenology Observation Annotations"
DEFAULT_LICENSE = "cc-by-4.0"
DEFAULT_CREATOR = "Phenobase Project"
DEFAULT_KEYWORDS = [
    "phenology",
    "plant phenology",
    "plant traits",
    "PPO",
    "biodiversity",
    "observations",
]
DEFAULT_SAMPLE_SOURCE_ORDER = [
    "Budburst",
    "Herbarium",
    "National Ecological Observatory Network (USA)",
    "PhenoObs",
    "SeasonWatch (India)",
    "SeasonWatch India",
    "USA National Phenology Network",
    "iNaturalist",
]


def parse_args():
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(
        description="Export Phenobase and build a compressed Zenodo-ready data package."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Phenobase query API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--index", default=DEFAULT_INDEX, help=f"Elasticsearch index name to query (default: {DEFAULT_INDEX})")
    parser.add_argument("--query", default=DEFAULT_QUERY, help=f"Lucene query string for export (default: {DEFAULT_QUERY})")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Hits per scroll page (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--scroll", default=DEFAULT_SCROLL, help=f"Scroll keepalive (default: {DEFAULT_SCROLL})")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to export. Use 0 for all rows (default: 0).")
    parser.add_argument(
        "--sample-per-datasource",
        type=int,
        default=0,
        help=(
            "Export up to N records per dataSource instead of scrolling the full query. "
            "Also writes live_dataset_counts.csv with exact live counts."
        ),
    )
    parser.add_argument("--request-timeout", type=float, default=DEFAULT_REQUEST_TIMEOUT, help=f"Per-request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT})")
    parser.add_argument("--columns-path", default=DEFAULT_COLUMNS_PATH, help=f"Column metadata CSV path (default: {DEFAULT_COLUMNS_PATH})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Destination parent directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--package-name", default=f"phenobase-zenodo-{today}", help="Package directory and ZIP basename")
    parser.add_argument("--title", default=DEFAULT_TITLE, help=f"Zenodo title template (default: {DEFAULT_TITLE})")
    parser.add_argument("--version", default=today, help="Dataset version string for package metadata")
    parser.add_argument("--publication-date", default=today, help="Publication date for Zenodo metadata template")
    parser.add_argument("--license", default=DEFAULT_LICENSE, help=f"Zenodo license id template (default: {DEFAULT_LICENSE})")
    parser.add_argument(
        "--creator",
        action="append",
        default=None,
        help="Creator name for Zenodo metadata. Repeat for multiple creators. Default: Phenobase Project",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="Keyword for Zenodo metadata. Repeat for multiple keywords. Defaults to Phenobase keywords.",
    )
    parser.add_argument(
        "--include-all-columns",
        action="store_true",
        help="Export every field in columns.csv instead of only fields with visible_on_archive=TRUE.",
    )
    parser.add_argument(
        "--skip-zip",
        action="store_true",
        help="Write the package directory but do not create the final ZIP archive.",
    )
    return parser.parse_args()


def parse_bool(value):
    return str(value or "").strip().lower() in {"true", "t", "yes", "y", "1"}


def ensure_clean_dir(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def load_column_metadata(columns_path, include_all_columns=False):
    rows = []
    fields = []
    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clean = {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
            field = clean.get("field", "")
            if not field:
                continue
            if include_all_columns or parse_bool(clean.get("visible_on_archive")):
                rows.append(clean)
                fields.append(field)

    if not fields:
        raise RuntimeError(f"No archive-visible fields found in {columns_path}")
    return rows, fields


def make_export_stats():
    return {
        "rows": 0,
        "data_sources": Counter(),
        "min_year": None,
        "max_year": None,
        "min_date": None,
        "max_date": None,
        "missing_sourceRecordUrl": 0,
        "skipped_year_only_herbarium": 0,
    }


def update_export_stats(stats, source, csv_row=None):
    csv_row = csv_row or {}
    stats["rows"] += 1
    data_source = csv_row.get("dataSource") or source.get("dataSource")
    if data_source:
        stats["data_sources"][str(data_source)] += 1

    year = csv_row.get("year") or source.get("year")
    try:
        year_int = int(year)
        stats["min_year"] = year_int if stats["min_year"] is None else min(stats["min_year"], year_int)
        stats["max_year"] = year_int if stats["max_year"] is None else max(stats["max_year"], year_int)
    except Exception:
        pass

    obs_date = csv_row.get("date") or source.get("date")
    if obs_date:
        obs_date = str(obs_date)
        stats["min_date"] = obs_date if stats["min_date"] is None else min(stats["min_date"], obs_date)
        stats["max_date"] = obs_date if stats["max_date"] is None else max(stats["max_date"], obs_date)

    if not (
        csv_row.get("sourceRecordUrl")
        or source.get("sourceRecordUrl")
        or source.get("observedMetadataUrl")
        or source.get("observed_metadata_url")
    ):
        stats["missing_sourceRecordUrl"] += 1


def print_page_progress(page_number, page_hits, total_written, expected_total, page_elapsed):
    if expected_total:
        print(
            f"Page {page_number}: fetched {page_hits:,} rows in {page_elapsed:.1f}s; "
            f"wrote {total_written:,}/{expected_total:,} total rows.",
            flush=True,
        )
    else:
        print(
            f"Page {page_number}: fetched {page_hits:,} rows in {page_elapsed:.1f}s; "
            f"wrote {total_written:,} total rows.",
            flush=True,
        )


def export_observations(args, csv_gz_path, field_order):
    stats = make_export_stats()

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
    page_number = 0

    with gzip.open(csv_gz_path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()

        while True:
            page_number += 1
            page_started_at = time.monotonic()
            hits = ((response or {}).get("hits") or {}).get("hits") or []
            if not hits:
                print(f"Page {page_number}: fetched 0 rows; export complete.", flush=True)
                break

            for hit in hits:
                if args.limit > 0 and stats["rows"] >= args.limit:
                    print(f"Reached client-side limit of {args.limit:,} rows.", flush=True)
                    return stats, expected_total
                source = hit.get("_source") or {}
                if should_skip_source(source):
                    stats["skipped_year_only_herbarium"] += 1
                    continue
                csv_row = build_csv_row(source, field_order)
                writer.writerow(csv_row)
                update_export_stats(stats, source, csv_row)

            fh.flush()
            page_elapsed = time.monotonic() - page_started_at
            print_page_progress(page_number, len(hits), stats["rows"], expected_total, page_elapsed)

            if args.limit > 0 and stats["rows"] >= args.limit:
                break
            if not scroll_id:
                break

            response = fetch_scroll_page(args.base_url, scroll_id, args.scroll, args.request_timeout)
            scroll_id = response.get("_scroll_id")

    return stats, expected_total


def build_query_clause(query):
    if query.strip() in {"", "*"}:
        return {"match_all": {}}
    return {
        "query_string": {
            "query": query,
            "analyze_wildcard": True,
        }
    }


def build_sample_query(query, data_source):
    base_query = build_query_clause(query)
    filters = [{"term": {"dataSource": data_source}}]
    if "match_all" in base_query:
        return {"bool": {"filter": filters}}
    return {"bool": {"must": [base_query], "filter": filters}}


def post_es_search(args, body):
    url = build_es_url(args.base_url, f"{args.index}/_search")
    return post_json(url, body, args.request_timeout)


def collect_live_dataset_counts(args):
    response = post_es_search(
        args,
        {
            "size": 0,
            "track_total_hits": True,
            "query": build_query_clause(args.query),
            "aggs": {
                "data_sources": {
                    "terms": {
                        "field": "dataSource",
                        "size": 1000,
                        "order": {"_key": "asc"},
                    }
                }
            },
        },
    )
    buckets = (((response or {}).get("aggregations") or {}).get("data_sources") or {}).get("buckets") or []
    return OrderedDict((bucket["key"], int(bucket.get("doc_count") or 0)) for bucket in buckets)


def ordered_sample_sources(live_counts):
    ordered = [source for source in DEFAULT_SAMPLE_SOURCE_ORDER if source in live_counts]
    ordered.extend(source for source in live_counts if source not in set(ordered))
    return ordered


def fetch_datasource_sample(args, data_source):
    rows = []
    offset = 0
    skipped = 0
    live_total = None
    first_id = ""
    last_id = ""
    page_size = max(args.batch_size, args.sample_per_datasource)

    while len(rows) < args.sample_per_datasource:
        response = post_es_search(
            args,
            {
                "from": offset,
                "size": page_size,
                "track_total_hits": True,
                "sort": ["_doc"],
                "query": build_sample_query(args.query, data_source),
            },
        )
        if live_total is None:
            live_total = get_total_hits(response)
        hits = (((response or {}).get("hits") or {}).get("hits") or [])
        if not hits:
            break

        for hit in hits:
            source = hit.get("_source") or {}
            if should_skip_source(source):
                skipped += 1
                continue
            rows.append(source)
            annotation_id = str(source.get("annotationID") or "")
            if not first_id:
                first_id = annotation_id
            last_id = annotation_id
            if len(rows) >= args.sample_per_datasource:
                break
        offset += len(hits)

    return rows, int(live_total or 0), skipped, first_id, last_id


def export_sampled_observations(args, csv_gz_path, field_order):
    stats = make_export_stats()
    live_counts = collect_live_dataset_counts(args)
    live_rows = []

    with gzip.open(csv_gz_path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()
        for data_source in ordered_sample_sources(live_counts):
            rows, live_total, skipped, first_id, last_id = fetch_datasource_sample(args, data_source)
            stats["skipped_year_only_herbarium"] += skipped
            live_rows.append(
                OrderedDict(
                    [
                        ("dataSource", data_source),
                        ("liveRecordCount", live_total or live_counts.get(data_source, 0)),
                        ("includedRecordCount", len(rows)),
                        ("firstSampleAnnotationID", first_id),
                        ("lastSampleAnnotationID", last_id),
                    ]
                )
            )
            print(
                f"{data_source}: live={live_rows[-1]['liveRecordCount']:,}; "
                f"included={len(rows):,}; skipped={skipped:,}",
                flush=True,
            )
            for source in rows:
                csv_row = build_csv_row(source, field_order)
                writer.writerow(csv_row)
                update_export_stats(stats, source, csv_row)

    expected_total = sum(row["liveRecordCount"] for row in live_rows)
    return stats, expected_total, live_rows


def write_data_dictionary(path, column_rows):
    fieldnames = list(column_rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in column_rows:
            writer.writerow(row)


def write_column_metadata_json(path, column_rows):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(column_rows, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def write_source_summary(path, data_sources):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["dataSource", "recordCount"])
        writer.writeheader()
        for data_source, count in sorted(data_sources.items()):
            writer.writerow({"dataSource": data_source, "recordCount": count})


def write_live_dataset_counts(path, live_rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "dataSource",
                "liveRecordCount",
                "includedRecordCount",
                "firstSampleAnnotationID",
                "lastSampleAnnotationID",
            ],
        )
        writer.writeheader()
        for row in live_rows:
            writer.writerow(row)


def description_html():
    return (
        "Phenobase observation annotations integrate plant phenological trait records from "
        "multiple source datasets and map them to Plant Phenology Ontology (PPO) trait "
        "identifiers. The package includes a compressed CSV export, column-level metadata, "
        "source record counts, checksums, and package documentation."
    )


def write_zenodo_metadata(path, args, stats):
    creators = args.creator or [DEFAULT_CREATOR]
    keywords = args.keyword or DEFAULT_KEYWORDS
    metadata = OrderedDict(
        [
            ("metadata", OrderedDict(
                [
                    ("title", args.title),
                    ("upload_type", "dataset"),
                    ("description", description_html()),
                    ("creators", [OrderedDict([("name", creator)]) for creator in creators]),
                    ("publication_date", args.publication_date),
                    ("license", args.license),
                    ("keywords", keywords),
                    ("version", args.version),
                    (
                        "notes",
                        "Template metadata generated for Zenodo submission. Review creators, "
                        "affiliations, funders, related identifiers, communities, and license before upload.",
                    ),
                    (
                        "related_identifiers",
                        [
                            OrderedDict(
                                [
                                    ("identifier", "https://github.com/phenobase/phenobase_data"),
                                    ("relation", "isSupplementedBy"),
                                    ("scheme", "url"),
                                ]
                            )
                        ],
                    ),
                    (
                        "custom",
                        OrderedDict(
                            [
                                ("record_count", stats["rows"]),
                                ("date_range", [stats["min_date"], stats["max_date"]]),
                                ("year_range", [stats["min_year"], stats["max_year"]]),
                            ]
                        ),
                    ),
                ]
            )),
        ]
    )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def write_summary_json(path, args, stats, expected_total, field_order):
    export_mode = "sample_per_datasource" if args.sample_per_datasource > 0 else "scroll"
    payload = OrderedDict(
        [
            ("generatedAtUtc", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            ("baseUrl", args.base_url),
            ("index", args.index),
            ("query", args.query),
            ("exportMode", export_mode),
            ("samplePerDataSource", args.sample_per_datasource),
            ("expectedTotalFromApi", expected_total),
            ("rowsExported", stats["rows"]),
            ("limit", args.limit),
            ("fieldCount", len(field_order)),
            ("fields", field_order),
            ("dateRange", [stats["min_date"], stats["max_date"]]),
            ("yearRange", [stats["min_year"], stats["max_year"]]),
            ("missingSourceRecordUrl", stats["missing_sourceRecordUrl"]),
            ("skippedYearOnlyHerbariumRecords", stats["skipped_year_only_herbarium"]),
            ("sourceCounts", OrderedDict(sorted(stats["data_sources"].items()))),
        ]
    )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def write_readme(path, args, stats, field_order):
    lines = [
        "# Phenobase Zenodo Data Package",
        "",
        f"Title: {args.title}",
        f"Version: {args.version}",
        f"Publication date: {args.publication_date}",
        f"Generated UTC: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}",
        "",
        "## Files",
        "",
        "- `phenobase_observations.csv.gz`: compressed CSV export of Phenobase records.",
        "- `data_dictionary.csv`: column-level metadata derived from `data/columns.csv`.",
        "- `column_metadata.json`: JSON representation of the same column metadata.",
        "- `source_summary.csv`: exported record counts by `dataSource`.",
        "- `record_summary.json`: export parameters and record counts.",
        "- `zenodo_metadata.json`: editable Zenodo deposition metadata template.",
        "- `manifest-sha256.txt`: SHA-256 checksums and byte sizes for package files.",
        "",
        "## CSV",
        "",
        f"Rows exported: {stats['rows']:,}",
        f"Fields exported: {len(field_order):,}",
        f"Date range: {stats['min_date'] or ''} to {stats['max_date'] or ''}",
        f"Year range: {stats['min_year'] or ''} to {stats['max_year'] or ''}",
        f"Records missing sourceRecordUrl: {stats['missing_sourceRecordUrl']:,}",
        f"Skipped year-only herbarium records: {stats['skipped_year_only_herbarium']:,}",
        "",
        "CSV arrays are pipe-delimited inside a cell. Nested objects, if any, are JSON-encoded inside a cell.",
        "",
        "## Generation Command",
        "",
        "```bash",
        "python3 build_zenodo_package.py \\",
        f"  --query {json.dumps(args.query)} \\",
        f"  --package-name {json.dumps(args.package_name)}" + (" \\" if args.sample_per_datasource > 0 else ""),
        *([f"  --sample-per-datasource {args.sample_per_datasource}"] if args.sample_per_datasource > 0 else []),
        "```",
        "",
        "## Zenodo Notes",
        "",
        "Review `zenodo_metadata.json` before upload. Creators, affiliations, funders, related identifiers, "
        "communities, and license should be confirmed by the submitting team. Once a Zenodo record is "
        "published, uploaded files cannot be modified; publish a new version for updated files.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path, package_dir):
    entries = []
    for file_path in sorted(package_dir.iterdir()):
        if not file_path.is_file() or file_path.name == path.name:
            continue
        rel = file_path.name
        entries.append((sha256_file(file_path), file_path.stat().st_size, rel))
    with open(path, "w", encoding="utf-8") as fh:
        for digest, size, rel in entries:
            fh.write(f"{digest}  {size}  {rel}\n")


def create_zip(package_dir, zip_path):
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for file_path in sorted(package_dir.iterdir()):
            if file_path.is_file():
                zf.write(file_path, arcname=f"{package_dir.name}/{file_path.name}")


def validate_args(args):
    if args.batch_size <= 0:
        raise RuntimeError("--batch-size must be greater than 0.")
    if args.limit < 0:
        raise RuntimeError("--limit must be 0 or greater.")
    if args.sample_per_datasource < 0:
        raise RuntimeError("--sample-per-datasource must be 0 or greater.")
    if args.limit > 0 and args.sample_per_datasource > 0:
        raise RuntimeError("--limit and --sample-per-datasource cannot be used together.")
    if args.request_timeout <= 0:
        raise RuntimeError("--request-timeout must be greater than 0.")


def main():
    args = parse_args()
    try:
        validate_args(args)
        output_dir = Path(args.output_dir)
        package_dir = output_dir / args.package_name
        zip_path = output_dir / f"{args.package_name}.zip"
        ensure_clean_dir(package_dir)

        column_rows, field_order = load_column_metadata(args.columns_path, args.include_all_columns)
        csv_gz_path = package_dir / "phenobase_observations.csv.gz"

        live_rows = None
        if args.sample_per_datasource > 0:
            stats, expected_total, live_rows = export_sampled_observations(args, csv_gz_path, field_order)
        else:
            stats, expected_total = export_observations(args, csv_gz_path, field_order)

        write_data_dictionary(package_dir / "data_dictionary.csv", column_rows)
        write_column_metadata_json(package_dir / "column_metadata.json", column_rows)
        write_source_summary(package_dir / "source_summary.csv", stats["data_sources"])
        if live_rows is not None:
            write_live_dataset_counts(package_dir / "live_dataset_counts.csv", live_rows)
        write_summary_json(package_dir / "record_summary.json", args, stats, expected_total, field_order)
        write_zenodo_metadata(package_dir / "zenodo_metadata.json", args, stats)
        write_readme(package_dir / "README.md", args, stats, field_order)
        write_manifest(package_dir / "manifest-sha256.txt", package_dir)

        if not args.skip_zip:
            create_zip(package_dir, zip_path)
            print(f"Saved ZIP package to {zip_path.resolve()}")
        print(f"Saved package directory to {package_dir.resolve()}")
        print(f"Rows exported: {stats['rows']:,}")
        print(f"Fields exported: {len(field_order):,}")
        return 0
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
        print(f"Network timeout: API did not respond within {args.request_timeout} seconds.", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
