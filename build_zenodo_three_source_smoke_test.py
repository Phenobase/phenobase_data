#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build a small Zenodo package with iNaturalist, Herbarium, and USA-NPN rows."""

from __future__ import annotations

import csv
import gzip
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from build_zenodo_package import (
    DEFAULT_CREATOR,
    DEFAULT_KEYWORDS,
    DEFAULT_LICENSE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TITLE,
    create_zip,
    ensure_clean_dir,
    load_column_metadata,
    make_export_stats,
    update_export_stats,
    validate_args,
    write_column_metadata_json,
    write_data_dictionary,
    write_manifest,
    write_readme,
    write_source_summary,
    write_summary_json,
    write_zenodo_metadata,
)
from download_csv_dump import (
    DEFAULT_BASE_URL,
    DEFAULT_INDEX,
    DEFAULT_REQUEST_TIMEOUT,
    build_csv_row,
    build_es_url,
    enrich_download_record,
    get_total_hits,
    post_json,
)
from trait_lookup import load_traits_catalog, mapped_traits_for_urn


SMOKE_SOURCES = [
    "iNaturalist",
    "Herbarium",
    "USA National Phenology Network",
]
PACKAGE_NAME = "phenobase-zenodo-three-source-smoke-test"


def parse_args():
    import argparse

    today = date.today().isoformat()
    parser = argparse.ArgumentParser(
        description="Build a Zenodo smoke-test package with records from each main source type."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--columns-path", default="data/columns.csv")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--package-name", default=PACKAGE_NAME)
    parser.add_argument("--rows", type=int, default=len(SMOKE_SOURCES), help="Total rows to export across the three source types.")
    parser.add_argument("--request-timeout", type=float, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--skip-zip", action="store_true")
    parser.add_argument("--version", default=today)
    parser.add_argument("--publication-date", default=today)
    return parser.parse_args()


def row_counts_by_source(total_rows):
    if total_rows < len(SMOKE_SOURCES):
        raise RuntimeError(f"--rows must be at least {len(SMOKE_SOURCES)}.")

    base = total_rows // len(SMOKE_SOURCES)
    remainder = total_rows % len(SMOKE_SOURCES)
    return {
        source: base + (1 if index < remainder else 0)
        for index, source in enumerate(SMOKE_SOURCES)
    }


def fetch_source_records(args, data_source, size):
    url = build_es_url(args.base_url, f"{args.index}/_search")
    body = {
        "size": size,
        "_source": True,
        "query": {
            "term": {
                "dataSource": data_source,
            },
        },
        "sort": [
            {
                "annotationID": {
                    "order": "asc",
                    "missing": "_last",
                },
            },
        ],
        "track_total_hits": True,
    }
    response = post_json(url, body, args.request_timeout)
    hits = ((response or {}).get("hits") or {}).get("hits") or []
    if len(hits) < size:
        raise RuntimeError(f"Only found {len(hits)} records for dataSource={data_source!r}; needed {size}.")
    return [hit.get("_source") or {} for hit in hits], get_total_hits(response)


def enrich_trait_fields(record, traits_catalog):
    enriched = dict(record or {})
    trait_urn = (
        str(enriched.get("traitUrn") or enriched.get("trait_urn") or "").strip()
    )
    mapped_ids, mapped_traits = mapped_traits_for_urn(trait_urn, traits_catalog)

    if mapped_ids and not enriched.get("mappedTraitsUrns"):
        enriched["mappedTraitsUrns"] = mapped_ids.split("|")
    if mapped_traits and not enriched.get("mappedTraits"):
        enriched["mappedTraits"] = mapped_traits.split("|")
    return enriched


def build_args_for_package(args):
    return SimpleNamespace(
        base_url=args.base_url,
        index=args.index,
        query=" OR ".join(f'dataSource:"{source}"' for source in SMOKE_SOURCES),
        limit=args.rows,
        sample_per_datasource=0,
        sample_per_datasource_trait_category=0,
        batch_size=args.rows,
        scroll="none",
        request_timeout=args.request_timeout,
        columns_path=args.columns_path,
        output_dir=args.output_dir,
        package_name=args.package_name,
        title=f"{DEFAULT_TITLE} Three-Source Smoke Test",
        version=args.version,
        publication_date=args.publication_date,
        license=DEFAULT_LICENSE,
        creator=[DEFAULT_CREATOR],
        keyword=DEFAULT_KEYWORDS,
        include_all_columns=False,
        skip_zip=args.skip_zip,
    )


def write_observations(csv_gz_path, field_order, records):
    stats = make_export_stats()

    with gzip.open(csv_gz_path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()
        for source in records:
            enriched = enrich_download_record(source)
            csv_row = build_csv_row(enriched, field_order)
            writer.writerow(csv_row)
            update_export_stats(stats, enriched, csv_row)

    return stats


def build_package(args, records, expected_total):
    package_args = build_args_for_package(args)
    validate_args(package_args)

    output_dir = Path(package_args.output_dir)
    package_dir = output_dir / package_args.package_name
    zip_path = output_dir / f"{package_args.package_name}.zip"
    ensure_clean_dir(package_dir)

    column_rows, field_order = load_column_metadata(package_args.columns_path)
    csv_gz_path = package_dir / "phenobase_observations.csv.gz"
    stats = write_observations(csv_gz_path, field_order, records)

    write_data_dictionary(package_dir / "data_dictionary.csv", column_rows)
    write_column_metadata_json(package_dir / "column_metadata.json", column_rows)
    write_source_summary(package_dir / "source_summary.csv", stats["data_sources"])
    write_summary_json(package_dir / "record_summary.json", package_args, stats, expected_total, field_order)
    write_zenodo_metadata(package_dir / "zenodo_metadata.json", package_args, stats)
    write_readme(package_dir / "README.md", package_args, stats, field_order)
    write_manifest(package_dir / "manifest-sha256.txt", package_dir)

    if not package_args.skip_zip:
        create_zip(package_dir, zip_path)
        print(f"Saved ZIP package to {zip_path.resolve()}")
    print(f"Saved package directory to {package_dir.resolve()}")
    print(f"Rows exported: {stats['rows']:,}")
    print(f"Fields exported: {len(field_order):,}")


def main():
    args = parse_args()
    validate_args(build_args_for_package(args))
    traits_catalog = load_traits_catalog()
    records = []
    expected_total = 0
    counts_by_source = row_counts_by_source(args.rows)

    for data_source, row_count in counts_by_source.items():
        source_records, source_total = fetch_source_records(args, data_source, row_count)
        records.extend(enrich_trait_fields(record, traits_catalog) for record in source_records)
        expected_total += source_total
        print(f"Fetched {data_source}: {row_count} row(s)")

    build_package(args, records, expected_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
