#!/usr/bin/env python3
import csv
import os
import zipfile
import xml.etree.ElementTree as ET
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from trait_lookup import load_traits_catalog

RAW_ROOT = BASE_DIR
COORDS_XLSX = os.path.join(BASE_DIR, "Coordinates_PhenObs_Gardens.xlsx")
MAPPINGS_CSV = os.path.join(BASE_DIR, "mappings.csv")
OUT_DIR = os.path.join(BASE_DIR, "ingest")
TRAITS_CATALOG = load_traits_catalog(os.path.join(REPO_ROOT, "data", "traits.csv"))
TRAITS_BY_URN = TRAITS_CATALOG["by_urn"]

DATA_SOURCE = "PhenoObs"
ANNOTATION_METHOD = "in_situ"
YES_VALUES = {"y", "yes", "1", "true"}
NO_VALUES = {"n", "no", "0", "false"}

VALID_PRESENT = {"y", "yes"}
VALID_ABSENT = {"no", "n"}


def _col_letters_to_index(letters):
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def read_coords_xlsx(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing coordinates file: {path}")

    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            tree = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in tree.findall(".//s:si", ns):
                parts = []
                for t in si.findall(".//s:t", ns):
                    parts.append(t.text or "")
                shared.append("".join(parts))

        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
        sheets.sort()
        if not sheets:
            raise ValueError("No worksheets found in coordinates file.")

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
        raise ValueError("No coordinate rows found in xlsx.")

    header = [h.strip() for h in rows[0]]
    coord_rows = rows[1:]

    name_idx = header.index("Botanic_Garden")
    lat_idx = header.index("Latitude")
    lon_idx = header.index("Longitude")

    coords = {}
    for r in coord_rows:
        if len(r) <= max(name_idx, lat_idx, lon_idx):
            continue
        name = (r[name_idx] or "").strip()
        if not name:
            continue
        lat = (r[lat_idx] or "").strip()
        lon = (r[lon_idx] or "").strip()
        if not lat or not lon:
            continue
        coords[name] = (float(lat), float(lon))
    return coords


def trait_label_for_urn(trait_urn):
    record = TRAITS_BY_URN.get(trait_urn)
    if not record:
        raise KeyError(f"Trait URN not found in traits.csv: {trait_urn}")
    return record["trait"]


def load_mappings(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing mappings file: {path}")

    mappings = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            field = (row.get("verbatim_trait") or row.get("source_field") or "").strip()
            if not field:
                continue
            status = (row.get("status") or "").strip().lower()
            trait_urn = (row.get("trait_urn") or "").strip()
            trait = (row.get("trait") or "").strip()
            mappings.setdefault(field, {})
            if status in YES_VALUES:
                mappings[field]["yes"] = {"trait_urn": trait_urn, "trait": trait}
            elif status in NO_VALUES:
                mappings[field]["no"] = {"trait_urn": trait_urn, "trait": trait}
    return mappings


def extract_genus(scientific_name):
    parts = scientific_name.split()
    return parts[0] if parts else ""


def normalize_date(value):
    value = (value or "").strip()
    if not value:
        return ""
    return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return ""


def raw_files(root):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.startswith("rawdata_PhenObs_") and name.endswith(".csv"):
                yield os.path.join(dirpath, name)


def build_rows(raw_path, coords, mappings):
    with open(raw_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            species = (row.get("Species") or "").strip()
            garden = (row.get("Botanic_Garden") or "").strip()
            species_id = (row.get("Species_ID") or "").strip()
            date_norm = normalize_date(row.get("Date"))
            if not species or not garden or not date_norm:
                continue

            year = safe_int(date_norm.split("-")[0])
            doy = safe_int(row.get("Doy"))

            lat_lon = coords.get(garden)
            if not lat_lon:
                continue
            lat, lon = lat_lon

            occurrence_id = f"phenoobs:{species_id}:{date_norm}"

            for col, status_map in mappings.items():
                raw_val = (row.get(col) or "").strip().lower()
                if raw_val in VALID_PRESENT:
                    decision = status_map.get("yes")
                elif raw_val in VALID_ABSENT:
                    decision = status_map.get("no")
                else:
                    continue

                if not decision:
                    continue

                trait_urn = decision.get("trait_urn", "")
                if not trait_urn:
                    raise KeyError(f"Missing trait_urn mapping for {col} value '{raw_val}' in {MAPPINGS_CSV}")

                trait = trait_label_for_urn(trait_urn)
                genus = extract_genus(species)
                annotation_id = f"{occurrence_id}:{trait_urn.replace(':', '_')}"

                verbatim = f"{col}={raw_val}"
                if col == "Flowers.opening":
                    intensity = (row.get("Flowering.intensity") or "").strip()
                    if intensity and intensity != "0":
                        verbatim = f"{verbatim}; Flowering.intensity={intensity}"
                if col == "Senescence":
                    intensity = (row.get("Senescence.intensity") or "").strip()
                    if intensity and intensity != "0":
                        verbatim = f"{verbatim}; Senescence.intensity={intensity}"

                yield {
                    "annotationID": annotation_id,
                    "dataSource": DATA_SOURCE,
                    "scientificName": species,
                    "genus": genus,
                    "trait_urn": trait_urn,
                    "trait": trait,
                    "family": "",
                    "year": year,
                    "dayOfYear": doy,
                    "latitude": lat,
                    "longitude": lon,
                    "annotation_method": ANNOTATION_METHOD,
                    "occurrenceID": occurrence_id,
                    "date": date_norm,
                    "verbatimTrait": verbatim,
                }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    coords = read_coords_xlsx(COORDS_XLSX)
    mappings = load_mappings(MAPPINGS_CSV)

    out_fields = [
        "annotationID",
        "dataSource",
        "scientificName",
        "genus",
        "trait_urn",
        "trait",
        "family",
        "year",
        "dayOfYear",
        "latitude",
        "longitude",
        "annotation_method",
        "occurrenceID",
        "date",
        "verbatimTrait",
    ]

    for raw_path in sorted(raw_files(RAW_ROOT)):
        year_dir = os.path.basename(os.path.dirname(raw_path))
        out_name = f"phenoObs_{year_dir}.csv"
        out_path = os.path.join(OUT_DIR, out_name)

        with open(out_path, "w", newline="", encoding="utf-8") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=out_fields)
            writer.writeheader()
            for row in build_rows(raw_path, coords, mappings):
                writer.writerow(row)

        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
