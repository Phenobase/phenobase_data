"""Shared Phenobase export field projection.

Portal downloads and Zenodo packages use this module so their CSV headers and
record values stay aligned even while the internal Elasticsearch schema remains
backward compatible with older ingest field names.
"""

from __future__ import annotations

import csv
import re


FIELD_RENAMES = {
    "date": "eventDate",
    "locationID": "siteID",
    "observedMetadataUrl": "sourceRecordUrl",
    "trait_urn": "traitUrn",
    "annotation_method": "annotationMethod",
    "basisOfRecord": "input",
}

EXCLUDED_FIELDS = {
    "taxonSearch",
    "decadeStart",
    "proportionCertaintyFamily",
    "countFamily",
    "Certainty",
    "certainty",
    "errorMessage",
    "predictionProbability",
    "preditionProbability",
    "predictionClass",
    "accuracyFamily",
}

ADDITIONAL_FIELD_METADATA = {
    "mappedTraitUrn": {
        "field": "mappedTraitUrn",
        "visible_on_portal": "FALSE",
        "visible_on_download": "TRUE",
        "visible_on_archive": "TRUE",
        "machine_annotation_inat_relevance": "APPLICABLE",
        "machine_annotation_herbarium_relevance": "APPLICABLE",
        "in_situ_relevance": "APPLICABLE",
        "alias": "mappedTraitIDs",
        "definedBy": "https://biscicol.org/api/v1/inaan/ark:/92250/mappedTraitUrn?info",
        "datatype": "keyword",
        "source": "system",
        "definition": "System inferred list of mapped trait URNs corresponding to mappedTraits, pipe-delimited in CSV exports.",
    },
}

INPUT_DEFINITION = (
    "Description of the source input used to create the annotation, such as "
    "human observation, live plant image, or herbarium specimen image."
)


def parse_bool(value):
    return str(value or "").strip().lower() in {"true", "t", "yes", "y", "1"}


def export_field_name(source_field):
    return FIELD_RENAMES.get(source_field, source_field)


def load_export_column_metadata(columns_path, include_all_columns=False):
    rows = []
    fields = []
    seen = set()

    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        for row in reader:
            clean = {
                key: (value.strip() if isinstance(value, str) else value)
                for key, value in row.items()
            }
            source_field = clean.get("field", "")
            if not source_field or source_field in EXCLUDED_FIELDS:
                continue
            if not include_all_columns and not parse_bool(clean.get("visible_on_download")):
                continue

            exported_field = export_field_name(source_field)
            if exported_field in seen:
                continue

            clean["field"] = exported_field
            if exported_field == "sourceRecordUrl":
                clean["alias"] = "observedMetadataUrl"
                clean["definedBy"] = "https://biscicol.org/api/v1/inaan/ark:/92250/sourceRecordUrl?info"
                clean["definition"] = "URL for the source record hosted by the contributing datasource."
            elif exported_field == "eventDate":
                clean["definedBy"] = "http://rs.tdwg.org/dwc/terms/eventDate"
                clean["definition"] = "Date of the observation event."
            elif exported_field == "siteID":
                clean["alias"] = "locationID"
                clean["definedBy"] = "https://biscicol.org/api/v1/inaan/ark:/92250/siteID?info"
                clean["definition"] = "Identifier for the observation site."
            elif exported_field == "traitUrn":
                clean["alias"] = "trait_urn"
                clean["definedBy"] = "https://biscicol.org/api/v1/inaan/ark:/92250/traitUrn?info"
            elif exported_field == "annotationMethod":
                clean["alias"] = "annotation_method"
                clean["definedBy"] = "https://biscicol.org/api/v1/inaan/ark:/92250/annotationMethod?info"
                clean["definition"] = "Method of annotation process, such as human or machine."
            elif exported_field == "input":
                clean["alias"] = "basisOfRecord"
                clean["definedBy"] = "https://biscicol.org/api/v1/inaan/ark:/92250/input?info"
                clean["definition"] = INPUT_DEFINITION

            rows.append(clean)
            fields.append(exported_field)
            seen.add(exported_field)

            if exported_field == "mappedTraits" and "mappedTraitUrn" not in seen:
                meta = {key: "" for key in fieldnames}
                meta.update(ADDITIONAL_FIELD_METADATA["mappedTraitUrn"])
                rows.append(meta)
                fields.append("mappedTraitUrn")
                seen.add("mappedTraitUrn")

    if "mappedTraits" in seen and "mappedTraitUrn" not in seen:
        meta = dict(ADDITIONAL_FIELD_METADATA["mappedTraitUrn"])
        rows.append(meta)
        fields.append("mappedTraitUrn")

    return rows, fields


def load_export_field_order(columns_path, include_all_columns=False):
    _rows, fields = load_export_column_metadata(columns_path, include_all_columns)
    return fields


def _clean(value):
    return "" if value is None else str(value).strip()


def first_value(record, *fields):
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return ""


def normalize_annotation_method(value):
    text = _clean(value)
    key = text.lower()
    if key in {"in_situ", "in situ", "human observation"}:
        return "human"
    return text


def derive_annotation_method(record):
    explicit = normalize_annotation_method(first_value(record, "annotationMethod", "annotation_method"))
    if explicit:
        return explicit

    if derive_input(record) == "human observation":
        return "human"
    return ""


def derive_input(record):
    existing = _clean(record.get("input"))
    if existing:
        return existing

    source = _clean(record.get("dataSource")).lower()
    method = normalize_annotation_method(first_value(record, "annotationMethod", "annotation_method")).lower()
    basis = _clean(record.get("basisOfRecord")).lower()

    if "herbarium" in source:
        return "herbarium specimen image"
    if "inaturalist" in source or source == "inat" or "inat" in source:
        return "live plant image"
    if method == "human" or basis == "human observation":
        return "human observation"
    if basis:
        return basis
    return ""


def derive_occurrence_id(record):
    existing = first_value(record, "occurrenceID")
    if existing:
        return existing

    annotation_id = _clean(record.get("annotationID"))
    source = _clean(record.get("dataSource")).lower()
    if annotation_id.startswith("npn:"):
        return annotation_id[4:]
    if annotation_id.isdigit() and "national phenology network" in source:
        return annotation_id
    return ""


def normalize_verbatim_trait(value, record=None):
    text = _clean(value)
    record = record or {}

    if text:
        if "=" in text:
            parts = [part.strip() for part in text.split(";")]
            normalized = []
            for part in parts:
                if "=" in part:
                    left, right = part.split("=", 1)
                    normalized.append(f"{left.strip()} = {right.strip()}")
                else:
                    normalized.append(part)
            return "; ".join(normalized)

        match = re.match(r"^(?P<label>.+?)\s*\((?P<value>[^()]*)\)\s*$", text)
        if match:
            label = match.group("label").strip()
            raw_value = match.group("value").strip()
            if re.fullmatch(r"-?\d+(?:\.\d+)?|present|absent|observed|not observed|yes|no", raw_value, re.IGNORECASE):
                return f"{label} = {raw_value}"

        plant_group = _clean(record.get("plant_group_id"))
        phenophase = _clean(record.get("phenophase_id"))
        if plant_group and phenophase and text == f"{plant_group}:{phenophase}":
            return f"plantGroup = {plant_group}; phenophase = {phenophase}"

        text_match = re.match(r"^(?P<label>.+?)\s+(?P<value>present|absent)$", text, re.IGNORECASE)
        if text_match:
            return f"{text_match.group('label').strip()} = {text_match.group('value').lower()}"

        trait = _clean(record.get("trait"))
        trait_match = re.match(r"^(?P<label>.+?)\s+(?P<value>present|absent)$", trait, re.IGNORECASE)
        if trait_match and text.lower() == trait_match.group("label").strip().lower():
            return f"{text} = {trait_match.group('value').lower()}"
        if trait_match:
            return f"{text} = {trait_match.group('value').lower()}"

    return text


def project_export_record(record, field_order):
    record = dict(record or {})
    projected = {}

    for field in field_order:
        if field == "eventDate":
            value = first_value(record, "eventDate", "date")
        elif field == "sourceRecordUrl":
            value = first_value(record, "sourceRecordUrl", "observedMetadataUrl")
        elif field == "siteID":
            value = first_value(record, "siteID", "locationID", "site_id")
        elif field == "traitUrn":
            value = first_value(record, "traitUrn", "trait_urn")
        elif field == "annotationMethod":
            value = derive_annotation_method(record)
        elif field == "input":
            value = derive_input(record)
        elif field == "organismID":
            value = first_value(record, "organismID", "individual_id")
        elif field == "occurrenceID":
            value = derive_occurrence_id(record)
        elif field == "mappedTraitUrn":
            value = first_value(record, "mappedTraitUrn", "mappedTraitUrns", "mappedTraitIDs")
        elif field == "verbatimTrait":
            value = normalize_verbatim_trait(first_value(record, "verbatimTrait"), record)
        else:
            value = record.get(field)
        projected[field] = value

    return projected
