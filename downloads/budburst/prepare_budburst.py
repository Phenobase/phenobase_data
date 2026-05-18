#!/usr/bin/env python3
"""Transform Budburst observations into loader-ready Phenobase CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trait_lookup import canonical_label_for_urn, load_traits_catalog


DEFAULT_INPUT = BASE_DIR / "budburst.csv"
DEFAULT_MAPPINGS = BASE_DIR / "mappings.csv"
DEFAULT_OUTPUT = BASE_DIR / "ingest" / "budburst_observations.csv"
TRAITS_CSV = REPO_ROOT / "data" / "traits.csv"

DATA_SOURCE = "Budburst"
ANNOTATION_METHOD = "in_situ"
BASIS_OF_RECORD = "Human Observation"

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
        f"from {path}. Missing PPO IDs in traits.csv: {missing_traits}"
    )
    return mappings


def parse_observation_date(value):
    value = norm(value)
    if not value:
        return None
    return date.fromisoformat(value)


def transform_row(row, mappings, counters, include_youth):
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

    latitude = norm(row.get("latitude"))
    longitude = norm(row.get("longitude"))
    if not latitude or not longitude:
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
    verbatim_trait = f"{plant_group}:{phenophase}"

    for idx, trait_record in enumerate(trait_records, start=1):
        trait_urn = trait_record["trait_urn"]
        trait = trait_record["trait"]
        annotation_id = f"budburst:{observation_id}:{plant_group}:{phenophase}:{idx}:{trait_urn.replace(':', '_')}"
        counters["keptCount"] += 1

        yield OrderedDict(
            [
                ("dataSource", DATA_SOURCE),
                ("scientificName", scientific_name),
                ("taxonRank", "species"),
                ("basisOfRecord", BASIS_OF_RECORD),
                ("family", ""),
                ("genus", extract_genus(scientific_name)),
                ("annotationID", annotation_id),
                ("date", obs_date.isoformat()),
                ("year", obs_date.year),
                ("dayOfYear", obs_date.timetuple().tm_yday),
                ("latitude", latitude),
                ("longitude", longitude),
                ("organismID", organism_id),
                ("occurrenceID", occurrence_id),
                ("annotation_method", ANNOTATION_METHOD),
                ("verbatimTrait", verbatim_trait),
                ("phenophase_id", phenophase),
                ("plant_group_id", plant_group),
                ("report_id", report_id),
                ("site_species_id", site_species_id),
                ("trait_urn", trait_urn),
                ("trait", trait),
            ]
        )


def main():
    parser = argparse.ArgumentParser(description="Transform Budburst raw observations into loader-ready CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Raw Budburst CSV from fetch_budburst.py.")
    parser.add_argument("--mappings", default=str(DEFAULT_MAPPINGS), help="Budburst PPO mapping CSV.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output loader-ready CSV.")
    parser.add_argument("--include-youth", action="store_true", help="Include youth observations. Default is to exclude them.")
    args = parser.parse_args()

    input_path = Path(args.input)
    mappings_path = Path(args.mappings)
    output_path = Path(args.output)

    traits_catalog = load_traits_catalog(str(TRAITS_CSV))
    mappings = load_mappings(mappings_path, traits_catalog)

    counters = {
        "rawRows": 0,
        "keptCount": 0,
        "droppedYouth": 0,
        "droppedMissingScientificName": 0,
        "droppedBadDate": 0,
        "droppedMissingCoords": 0,
        "droppedNoMap": 0,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open(newline="", encoding="utf-8-sig") as in_f, output_path.open("w", newline="", encoding="utf-8") as out_f:
        reader = csv.DictReader(in_f)
        writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in reader:
            for transformed in transform_row(row, mappings, counters, args.include_youth):
                writer.writerow(transformed)

    print(f"Wrote {counters['keptCount']} row(s) to {output_path}")
    print(
        "Raw rows: {rawRows} | Kept rows: {keptCount} | Dropped youth: {droppedYouth} | "
        "Dropped missing scientific name: {droppedMissingScientificName} | Dropped bad date: {droppedBadDate} | "
        "Dropped missing coords: {droppedMissingCoords} | Dropped no map: {droppedNoMap}".format(**counters)
    )


if __name__ == "__main__":
    main()
