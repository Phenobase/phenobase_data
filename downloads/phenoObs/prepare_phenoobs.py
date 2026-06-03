#!/usr/bin/env python3
"""Transform PhenoObs raw exports into one loader-ready CSV."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trait_lookup import canonical_label_for_urn, load_traits_catalog


DEFAULT_COORDS_XLSX = BASE_DIR / "Coordinates_PhenObs_Gardens.xlsx"
DEFAULT_MAPPINGS_CSV = BASE_DIR / "mappings.csv"
TRAITS_CSV = REPO_ROOT / "data" / "traits.csv"
FALLBACK_RAW_ROOT = REPO_ROOT / "data" / "phenoObs"

DATA_SOURCE = "PhenoObs"
ANNOTATION_METHOD = "in_situ"
BASIS_OF_RECORD = "Human Observation"
PHENOOBS_DATA_ACCESS_URL = "https://www.idiv.de/research/projects/phenobs/data-access/"
PHENOOBS_METADATA_URLS_BY_YEAR = {
    "2019": "https://doi.org/10.25829/idiv.3519-a6r94f",
    "2020": "https://doi.org/10.25829/idiv.3535-6j8cmx",
    "2021": "https://doi.org/10.25829/idiv.3536-o94ra8",
    "2022": "https://doi.org/10.25829/idiv.3550-m3qf86",
    "2023": "https://doi.org/10.25829/idiv.3560-d86jz5",
    "2024": "https://doi.org/10.25829/idiv.3582-g9vb2e",
}

VALID_PRESENT = {"y", "yes", "1", "true"}
VALID_ABSENT = {"no", "n", "0", "false"}
NULL_VALUES = {"", "na", "n/a", "null", "none", "-", "u", "m"}

DEFAULT_COORDS = {
    "Berlin": ("52.454", "13.305"),
    "Edinburgh": ("55.965", "-3.209"),
    "Frankfurt": ("50.123", "8.656"),
    "Halle": ("51.488", "11.96"),
    "Jena": ("50.93", "11.585"),
    "Petrozavodsk": ("61.768", "34.401"),
    "Potsdam": ("52.404", "13.025"),
    "Prague": ("50.071", "14.42"),
    "Rome": ("41.892", "12.462"),
    "Srinagar": ("34.127", "74.832"),
    "Trondheim": ("63.446", "10.452"),
    "Tuebingen": ("48.539", "9.035"),
    "Vienna": ("48.192", "16.38"),
    "Xixon": ("43.52", "-5.614"),
}

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
    "dayOfYear",
    "latitude",
    "longitude",
    "observedMetadataUrl",
    "locationID",
    "organismID",
    "occurrenceID",
    "annotation_method",
    "verbatimTrait",
    "phenophase_status",
    "trait_urn",
    "trait",
]


def norm(value):
    return "" if value is None else str(value).strip()


def normalize_status(value):
    cleaned = norm(value).lower()
    if cleaned in VALID_PRESENT:
        return 1
    if cleaned in VALID_ABSENT:
        return 0
    if cleaned in NULL_VALUES:
        return None
    return None


def _col_letters_to_index(letters):
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def read_coords_xlsx(path):
    if not path.exists():
        return {}

    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            tree = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in tree.findall(".//s:si", ns):
                parts = [t.text or "" for t in si.findall(".//s:t", ns)]
                shared.append("".join(parts))

        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
        sheets.sort()
        if not sheets:
            raise ValueError(f"No worksheets found in coordinates file: {path}")

        sheet_xml = ET.fromstring(zf.read(sheets[0]))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = []
        for row in sheet_xml.findall(".//s:row", ns):
            row_vals = {}
            for cell in row.findall("s:c", ns):
                ref = cell.get("r", "")
                letters = "".join(ch for ch in ref if ch.isalpha())
                col_idx = _col_letters_to_index(letters) if letters else 0
                v = cell.find("s:v", ns)
                if v is None:
                    val = ""
                else:
                    val = v.text or ""
                    if cell.get("t") == "s":
                        try:
                            val = shared[int(val)]
                        except Exception:
                            pass
                row_vals[col_idx] = val
            if row_vals:
                max_idx = max(row_vals)
                row_list = [""] * (max_idx + 1)
                for idx, val in row_vals.items():
                    row_list[idx] = val
                rows.append(row_list)

    if not rows:
        return {}

    header = [h.strip() for h in rows[0]]
    name_idx = header.index("Botanic_Garden")
    lat_idx = header.index("Latitude")
    lon_idx = header.index("Longitude")

    coords = {}
    for row in rows[1:]:
        if len(row) <= max(name_idx, lat_idx, lon_idx):
            continue
        name = norm(row[name_idx])
        lat = norm(row[lat_idx])
        lon = norm(row[lon_idx])
        if name and lat and lon:
            coords[name] = (lat, lon)
    return coords


def load_coords(path):
    coords = dict(DEFAULT_COORDS)
    coords.update(read_coords_xlsx(path))
    return coords


def load_mappings(path, traits_catalog):
    if not path.exists():
        raise FileNotFoundError(f"Missing mappings file: {path}")

    mappings = {}
    rows = 0
    by_status = {"0": 0, "1": 0}
    invalid_status = 0

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows += 1
            field = norm(raw.get("verbatim_trait") or raw.get("source_field"))
            status = normalize_status(raw.get("status"))
            trait_urn = norm(raw.get("trait_urn") or raw.get("ppo_id") or raw.get("urn"))
            if not field:
                continue
            if status is None:
                invalid_status += 1
                continue
            if not trait_urn:
                raise KeyError(f"Missing trait_urn mapping for {field} status {raw.get('status')}")

            trait = canonical_label_for_urn(trait_urn, traits_catalog)
            if not trait:
                raise KeyError(f"Trait URN not found in traits.csv: {trait_urn}")

            mappings.setdefault(field, {})[status] = {
                "trait_urn": trait_urn,
                "trait": trait,
            }
            by_status[str(status)] += 1

    print(
        f"Loaded {rows} mapping row(s) across {len(mappings)} source field(s) from {path}. "
        f"Counts by status: 0={by_status['0']}, 1={by_status['1']}; invalid/blank={invalid_status}"
    )
    return mappings


def raw_files(root):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.startswith("rawdata_PhenObs_") and name.endswith(".csv"):
                yield Path(dirpath) / name


def auto_raw_root():
    for candidate in (BASE_DIR, FALLBACK_RAW_ROOT):
        if any(raw_files(candidate)):
            return candidate
    return BASE_DIR


def extract_genus(scientific_name):
    parts = scientific_name.split()
    return parts[0] if parts else ""


def extract_species(scientific_name):
    parts = scientific_name.split()
    return parts[1] if len(parts) > 1 else ""


def normalize_date(value):
    value = norm(value)
    if not value:
        return ""
    return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return ""


def make_verbatim_trait(field, raw_value, row):
    verbatim = f"{field}={raw_value}"
    if field == "Flowers.opening":
        intensity = norm(row.get("Flowering.intensity"))
        if intensity and intensity != "0":
            verbatim = f"{verbatim}; Flowering.intensity={intensity}"
    if field == "Senescence":
        intensity = norm(row.get("Senescence.intensity"))
        if intensity and intensity != "0":
            verbatim = f"{verbatim}; Senescence.intensity={intensity}"
    return verbatim


def phenoobs_metadata_url(raw_path):
    match = re.search(r"(20\d{2})", raw_path.name)
    if not match:
        return PHENOOBS_DATA_ACCESS_URL
    return PHENOOBS_METADATA_URLS_BY_YEAR.get(match.group(1), PHENOOBS_DATA_ACCESS_URL)


def transform_raw_row(row, mappings, coords, counters, source_row_id, observed_metadata_url):
    counters["rawRows"] += 1

    scientific_name = norm(row.get("Species"))
    garden = norm(row.get("Botanic_Garden"))
    organism_id = norm(row.get("Species_ID"))
    if not scientific_name or not garden or not organism_id:
        counters["droppedMissingCore"] += 1
        return

    try:
        date_norm = normalize_date(row.get("Date"))
    except Exception:
        counters["droppedBadDate"] += 1
        return
    if not date_norm:
        counters["droppedBadDate"] += 1
        return

    lat_lon = coords.get(garden)
    if not lat_lon:
        counters["droppedMissingCoords"] += 1
        return
    lat, lon = lat_lon

    year = safe_int(date_norm.split("-")[0])
    day_of_year = safe_int(row.get("Doy"))
    occurrence_id = f"phenoobs:{organism_id}:{date_norm}"
    genus = extract_genus(scientific_name)
    species = extract_species(scientific_name)

    for field, status_map in mappings.items():
        raw_value = norm(row.get(field))
        status = normalize_status(raw_value)
        if status is None:
            counters["droppedBadObsStatus"] += 1
            continue

        trait_record = status_map.get(status)
        if not trait_record:
            counters["droppedNoMap"] += 1
            continue

        trait_urn = trait_record["trait_urn"]
        trait = trait_record["trait"]
        annotation_id = f"{occurrence_id}:{field}:{status}:{trait_urn.replace(':', '_')}:{source_row_id}"

        counters["keptCount"] += 1
        yield OrderedDict(
            [
                ("dataSource", DATA_SOURCE),
                ("scientificName", scientific_name),
                ("taxonRank", "species"),
                ("basisOfRecord", BASIS_OF_RECORD),
                ("family", ""),
                ("genus", genus),
                ("species", species),
                ("annotationID", annotation_id),
                ("date", date_norm),
                ("year", year),
                ("dayOfYear", day_of_year),
                ("latitude", lat),
                ("longitude", lon),
                ("observedMetadataUrl", observed_metadata_url),
                ("locationID", garden),
                ("organismID", organism_id),
                ("occurrenceID", occurrence_id),
                ("annotation_method", ANNOTATION_METHOD),
                ("verbatimTrait", make_verbatim_trait(field, raw_value, row)),
                ("phenophase_status", "Observed" if status == 1 else "Not Observed"),
                ("trait_urn", trait_urn),
                ("trait", trait),
            ]
        )


def transform_file(raw_path, writer, mappings, coords, counters):
    print(f"Processing {raw_path}...")
    observed_metadata_url = phenoobs_metadata_url(raw_path)
    with raw_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        source_name = raw_path.parent.name
        for row_number, row in enumerate(reader, start=2):
            source_row_id = f"{source_name}:row{row_number}"
            for transformed in transform_raw_row(
                row,
                mappings,
                coords,
                counters,
                source_row_id,
                observed_metadata_url,
            ):
                writer.writerow(transformed)


def main():
    parser = argparse.ArgumentParser(
        description="Transform PhenoObs raw observations into one loader-ready CSV."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Optional paths. Use one CSV path for mappings, one directory path for raw_root, "
            "or both as: <raw_root> <mappings_csv>."
        ),
    )
    parser.add_argument(
        "--raw-root",
        default=None,
        help="Directory containing rawdata_PhenObs_*.csv files. Defaults to auto-detecting downloads/phenoObs, then data/phenoObs.",
    )
    parser.add_argument(
        "--mappings",
        default=None,
        help="Trait mapping CSV. Defaults to downloads/phenoObs/mappings.csv.",
    )
    parser.add_argument(
        "--output",
        default="phenoObs_observations.csv",
        help="Single output CSV path. Defaults to phenoObs_observations.csv in the current directory.",
    )
    parser.add_argument(
        "--coords",
        default=str(DEFAULT_COORDS_XLSX),
        help="Optional coordinates xlsx with Botanic_Garden, Latitude, Longitude columns.",
    )
    args = parser.parse_args()

    raw_root_arg = args.raw_root
    mappings_arg = args.mappings
    if len(args.paths) == 1:
        only = Path(args.paths[0])
        if only.suffix.lower() == ".csv":
            mappings_arg = args.paths[0]
        else:
            raw_root_arg = args.paths[0]
    elif len(args.paths) == 2:
        raw_root_arg, mappings_arg = args.paths
    elif len(args.paths) > 2:
        parser.error("Expected at most two positional paths: <raw_root> <mappings_csv>")

    output_path = Path(args.output)
    raw_root = Path(raw_root_arg) if raw_root_arg else auto_raw_root()
    mappings_path = Path(mappings_arg) if mappings_arg else DEFAULT_MAPPINGS_CSV
    coords_path = Path(args.coords)

    print(f"Output file: {output_path}")
    print(f"Raw root: {raw_root}")
    print(f"Mappings file: {mappings_path}")

    traits_catalog = load_traits_catalog(str(TRAITS_CSV))
    mappings = load_mappings(mappings_path, traits_catalog)
    coords = load_coords(coords_path)
    files = sorted(raw_files(raw_root))
    if not files:
        raise FileNotFoundError(f"No rawdata_PhenObs_*.csv files found under {raw_root}")

    counters = {
        "rawRows": 0,
        "keptCount": 0,
        "droppedMissingCore": 0,
        "droppedBadDate": 0,
        "droppedMissingCoords": 0,
        "droppedBadObsStatus": 0,
        "droppedNoMap": 0,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for raw_path in files:
            transform_file(raw_path, writer, mappings, coords, counters)

    print("Data transformation complete.")
    print(
        "Raw rows: {rawRows} | Kept rows: {keptCount} | Dropped missing core: {droppedMissingCore} | "
        "Dropped bad date: {droppedBadDate} | Dropped missing coords: {droppedMissingCoords} | "
        "Dropped bad obs status: {droppedBadObsStatus} | Dropped no map: {droppedNoMap}".format(**counters)
    )


if __name__ == "__main__":
    main()
