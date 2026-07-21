#!/usr/bin/env python3
"""Fetch and transform USA-NPN observations into loader-ready CSV."""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from source_record_url import build_npn_observation_url


API_URL = "https://services.usanpn.org/npn_portal/observations/getObservations.json"
SPECIES_URL = "https://services.usanpn.org/npn_portal/species/getSpecies.json"
DEFAULT_MAPPINGS_PATH = "/mnt/data/mappings.csv"
TRAITS_PATH = Path(__file__).resolve().parents[2] / "data" / "traits.csv"

OUTPUT_FIELDS = [
    "dataSource",
    "scientificName",
    "taxonRank",
    "basisOfRecord",
    "family",
    "genus",
    "species",
    "annotationID",
    "date",
    "year",
    "dataset_id",
    "site_id",
    "individual_id",
    "locationID",
    "organismID",
    "occurrenceID",
    "dayOfYear",
    "latitude",
    "longitude",
    "observedMetadataUrl",
    "annotation_method",
    "recordedBy",
    "verbatimTrait",
    "phenophase_status",
    "trait_urn",
    "trait",
]

LEGACY_LABEL_ALIAS = {
    "new shoot system present": "PPO:0002301",
    "new shoot system absent": "PPO:0002600",
}


def norm(value):
    return "" if value is None else str(value).strip()


def normalize_key(value):
    return " ".join(norm(value).split()).lower()


def parse_date(value):
    return date.fromisoformat(norm(value))


def add_months(value, months):
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_chunks(start_date, end_date):
    current = parse_date(start_date)
    end = parse_date(end_date)
    while current <= end:
        chunk_end = min(add_months(current, 1), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def fetch_json(url, params=None, timeout=60):
    query = urllib.parse.urlencode(params or {}, doseq=True)
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(full_url, headers={"User-Agent": "Phenobase-NPN-Loader/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def load_traits_lookup(csv_path):
    by_urn = {}
    by_label = {}

    if not os.path.exists(csv_path):
        print(f"⚠️  Traits file not found at {csv_path}. Proceeding without PPO ID lookup.")
        return {"by_urn": by_urn, "by_label": by_label}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trait_urn = norm(row.get("trait_urn"))
            trait = norm(row.get("trait"))
            record = {
                "trait_urn": trait_urn,
                "trait": trait,
                "mappedTraitIDs": norm(row.get("mappedTraitIDs")),
                "mappedTraits": norm(row.get("mappedTraits")),
            }
            if trait_urn:
                by_urn[trait_urn] = record
            if trait:
                by_label[normalize_key(trait)] = record

    return {"by_urn": by_urn, "by_label": by_label}


def resolve_trait_record(raw, trait_lookup):
    explicit_urn = norm(raw.get("trait_urn") or raw.get("ppo_id") or raw.get("urn"))
    explicit_label = norm(raw.get("trait"))

    if explicit_urn:
        by_urn = trait_lookup["by_urn"].get(explicit_urn)
        if by_urn:
            return by_urn
        if explicit_label:
            return {"trait_urn": explicit_urn, "trait": explicit_label}
        return {"trait_urn": explicit_urn, "trait": ""}

    if explicit_label:
        by_label = trait_lookup["by_label"].get(normalize_key(explicit_label))
        if by_label:
            return by_label
        alias_urn = LEGACY_LABEL_ALIAS.get(normalize_key(explicit_label))
        if alias_urn:
            aliased = trait_lookup["by_urn"].get(alias_urn)
            if aliased:
                return aliased
            return {"trait_urn": alias_urn, "trait": explicit_label}
        return {"trait_urn": "", "trait": explicit_label}

    return {"trait_urn": "", "trait": ""}


def parse_status(raw):
    s = "" if raw is None else str(raw)
    s = "".join("-" if ch in "\u2212\u2012\u2013\u2014\u2015" else ch for ch in s)
    s = s.strip().lower()

    if s == "":
        return None
    if s in {"-1", "-1.0", "drop", "ignore", "omit"}:
        return -1
    if s in {"0", "0.0", "absent", "no"}:
        return 0
    if s in {"1", "1.0", "present", "yes", "observed"}:
        return 1

    try:
        n = int(float(s))
    except Exception:
        return None
    return n if n in {-1, 0, 1} else None


def load_mappings(csv_path, trait_lookup):
    index = {}
    rows = 0
    by_status = {"-1": 0, "0": 0, "1": 0}
    invalid_status = 0

    if not os.path.exists(csv_path):
        print(f"⚠️  Mappings file not found at {csv_path}. Proceeding with empty mappings.")
        return index

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows += 1
            key = normalize_key(raw.get("verbatim_trait"))
            status_num = parse_status(raw.get("status"))
            trait_record = resolve_trait_record(raw, trait_lookup)

            if not key:
                continue
            if status_num is None:
                invalid_status += 1
                continue

            table = index.setdefault(key, {})
            if status_num == -1:
                table["__DROP__"] = True
            table[status_num] = trait_record
            by_status[str(status_num)] += 1

    print(
        f"Loaded {rows} mapping row(s) across {len(index)} verbatim_trait value(s) from {csv_path}. "
        f"Counts by status: -1={by_status['-1']}, 0={by_status['0']}, 1={by_status['1']}; invalid/blank={invalid_status}"
    )
    return index


def resolve_trait_mapping(mapping_index, normalized_key, observed_status):
    table = mapping_index.get(normalized_key)
    if not table:
        return {"drop": "no-map"}

    if table.get("__DROP__") or (-1 in table):
        return {"drop": "minus1"}

    exact = table.get(observed_status)
    if exact is not None:
        return {"trait": exact} if exact else {"drop": "empty"}

    fb1 = table.get(1)
    if fb1:
        return {"trait": fb1}

    fb0 = table.get(0)
    if fb0:
        return {"trait": fb0}

    if 1 in table or 0 in table:
        return {"drop": "empty"}

    return {"drop": "no-map"}


def fetch_species_catalog():
    try:
        data = fetch_json(SPECIES_URL, params={"request_src": "custom_script"})
    except Exception as err:
        print(f"Failed to load species catalog: {err}")
        return {"by_id": {}, "by_gs": {}}

    by_id = {}
    by_gs = {}
    if isinstance(data, list):
        for item in data:
            try:
                species_id = int(item.get("species_id"))
            except Exception:
                continue
            genus = norm(item.get("genus"))
            species = norm(item.get("species"))
            record = {
                "species_id": species_id,
                "family": norm(item.get("family") or item.get("family_name")),
                "genus": genus,
                "species": species,
            }
            by_id[species_id] = record
            if genus and species:
                by_gs[f"{genus}|{species}".lower()] = record

    print(f"Loaded species catalog: {len(by_id)} by id, {len(by_gs)} by genus/species.")
    return {"by_id": by_id, "by_gs": by_gs}


def fetch_data(start_date, end_date):
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "request_src": "custom_script",
        "additional_field": [
            "dataset_id",
            "observedby_person_id",
            "submittedby_person_id",
            "updatedby_person_id",
            "partner_group",
        ],
    }
    try:
        print(f"Fetching data from API for dates: {start_date} to {end_date}...")
        data = fetch_json(API_URL, params=params)
        return data if isinstance(data, list) else []
    except Exception as err:
        print(f"Error fetching data for dates: {start_date} to {end_date}: {err}")
        return []


def observation_metadata_url(obs):
    return build_npn_observation_url(obs.get("observation_id"))


def recorded_by(obs):
    for field in (
        "recordedBy",
        "recorded_by",
        "observedby_person_id",
        "submittedby_person_id",
        "updatedby_person_id",
        "observer_id",
        "observerID",
        "observer_name",
        "observer",
        "user_id",
        "username",
        "person_id",
        "participant_id",
    ):
        value = norm(obs.get(field))
        if value:
            return value
    return ""


def resolve_data_source(obs):
    dataset_id = norm(obs.get("dataset_id"))
    partner_group = normalize_key(obs.get("partner_group"))

    if dataset_id == "16" or partner_group == "neon":
        return "National Ecological Observatory Network (USA)"
    return "USA National Phenology Network"


def write_rows(rows, output_path, write_header):
    mode = "w" if write_header else "a"
    with open(output_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def transform_rows(observations, mapping_index, species_catalog, counters):
    transformed = []

    for obs in observations:
        cleaned_description = norm(obs.get("phenophase_description"))
        key = normalize_key(cleaned_description)

        raw_status = parse_status(obs.get("phenophase_status"))
        if raw_status not in {0, 1}:
            counters["droppedBadObsStatus"] += 1
            continue

        decision = resolve_trait_mapping(mapping_index, key, raw_status)
        if decision.get("drop"):
            reason = decision["drop"]
            if reason == "minus1":
                counters["droppedByMinusOne"] += 1
            elif reason == "empty":
                counters["droppedEmptyTrait"] += 1
            else:
                counters["droppedNoMap"] += 1
            continue

        trait_record = decision.get("trait") or {}
        trait_urn = norm(trait_record.get("trait_urn"))
        trait = norm(trait_record.get("trait"))
        if not trait_urn or not trait:
            counters["droppedNoMap"] += 1
            continue

        scientific_name = f"{norm(obs.get('genus'))} {norm(obs.get('species'))}".strip()
        genus = norm(obs.get("genus"))
        species = norm(obs.get("species"))
        species_id = obs.get("species_id")

        sp_by_id = None
        try:
            sp_by_id = species_catalog["by_id"].get(int(species_id))
        except Exception:
            sp_by_id = None
        gs_key = f"{genus}|{species}".lower()
        sp_by_gs = species_catalog["by_gs"].get(gs_key)
        family = norm((sp_by_id or sp_by_gs or {}).get("family"))

        observation_date = norm(obs.get("observation_date"))
        try:
            obs_date = parse_date(observation_date)
        except Exception:
            obs_date = None

        if obs_date is None:
            counters["droppedNoMap"] += 1
            continue

        transformed.append(
            OrderedDict(
                [
                    ("dataSource", resolve_data_source(obs)),
                    ("scientificName", scientific_name),
                    ("taxonRank", "species"),
                    ("basisOfRecord", "Human Observation"),
                    ("family", family),
                    ("genus", genus),
                    ("species", species),
                    ("annotationID", f"npn:{obs.get('observation_id')}"),
                    ("date", observation_date),
                    ("year", obs_date.year),
                    ("dataset_id", obs.get("dataset_id")),
                    ("site_id", obs.get("site_id")),
                    ("individual_id", obs.get("individual_id")),
                    ("locationID", obs.get("site_id")),
                    ("organismID", obs.get("individual_id")),
                    ("occurrenceID", obs.get("observation_id")),
                    ("dayOfYear", obs.get("day_of_year")),
                    ("latitude", obs.get("latitude")),
                    ("longitude", obs.get("longitude")),
                    ("observedMetadataUrl", observation_metadata_url(obs)),
                    ("annotation_method", "in_situ"),
                    ("recordedBy", recorded_by(obs)),
                    ("verbatimTrait", f"{cleaned_description} ({raw_status})"),
                    ("phenophase_status", "Observed" if raw_status == 1 else "Not Observed"),
                    ("trait_urn", trait_urn),
                    ("trait", trait),
                ]
            )
        )
        counters["keptCount"] += 1

    return transformed


def main():
    parser = argparse.ArgumentParser(
        description="Fetch USA-NPN observations and transform phenophase descriptions into trait rows."
    )
    parser.add_argument("start_date", help="Start date in YYYY-MM-DD format")
    parser.add_argument("end_date", help="End date in YYYY-MM-DD format")
    parser.add_argument("mappings_csv_path", nargs="?", default=DEFAULT_MAPPINGS_PATH)
    args = parser.parse_args()

    output_path = f"npn_observations_{args.start_date}_to_{args.end_date}.csv"
    print(f"Output file: {output_path}")

    trait_lookup = load_traits_lookup(str(TRAITS_PATH))
    species_catalog = fetch_species_catalog()
    mapping_index = load_mappings(args.mappings_csv_path, trait_lookup)

    counters = {
        "keptCount": 0,
        "droppedByMinusOne": 0,
        "droppedEmptyTrait": 0,
        "droppedNoMap": 0,
        "droppedBadObsStatus": 0,
    }

    wrote_header = False
    for chunk_start, chunk_end in month_chunks(args.start_date, args.end_date):
        observations = fetch_data(chunk_start, chunk_end)
        if not observations:
            print(f"No data found for dates: {chunk_start} to {chunk_end}")
            continue

        rows = transform_rows(observations, mapping_index, species_catalog, counters)
        if rows:
            write_rows(rows, output_path, not wrote_header)
            wrote_header = True
            print(f"Chunk successfully written to {output_path}")
        else:
            print(f"All rows for {chunk_start} to {chunk_end} were dropped by mapping rules.")

    print("Data fetching and writing complete.")
    print(
        "Kept rows: {keptCount} | Dropped (-1 map): {droppedByMinusOne} | "
        "Dropped (empty trait): {droppedEmptyTrait} | Dropped (no map): {droppedNoMap} | "
        "Dropped (bad obs status): {droppedBadObsStatus}".format(**counters)
    )


if __name__ == "__main__":
    main()
