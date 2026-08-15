"""Shared Phenobase export field projection.

Portal downloads and Zenodo packages use this module so their CSV headers and
record values stay aligned while the Elasticsearch schema remains backward
compatible with older ingest field names.
"""

from __future__ import annotations

import csv
import re

from source_record_url import source_record_url_for_record
from trait_lookup import load_traits_catalog, resolve_trait


DOI_RESOLVER_PREFIX = "https://doi.org/"
DEFAULT_TRAITS_PATH = "data/traits.csv"
HUMAN_COLLECTION_METHOD = "human observation"
HERBARIUM_COLLECTION_METHOD = "herbarium specimen image"
LIVE_PLANT_COLLECTION_METHOD = "live plant image"
COLLECTION_METHOD_BY_KEY = {
    "humanobservation": HUMAN_COLLECTION_METHOD,
    "machineobservation": LIVE_PLANT_COLLECTION_METHOD,
    "preservedspecimen": HERBARIUM_COLLECTION_METHOD,
    "herbariumspecimenimage": HERBARIUM_COLLECTION_METHOD,
    "liveplantimage": LIVE_PLANT_COLLECTION_METHOD,
}
ANNOTATION_METHOD_BY_BASIS = {
    "humanobservation": "human",
    "machineobservation": "machine",
    "preservedspecimen": "machine",
    "herbariumspecimenimage": "machine",
    "liveplantimage": "machine",
}
ANNOTATION_METHOD_BY_KEY = {
    "insitu": "human",
    "human": "human",
    "machine": "machine",
}
FIELD_FALLBACKS = {
    "annotationMethod": ("annotation_method",),
    "sourceRecordUrl": (
        "observedMetadataUrl",
        "observedMetadataURL",
        "observed_metadata_url",
        "observationMetadataUrl",
        "observationMetadatUrl",
    ),
    "verbatimFamily": ("family", "verbatim_family"),
    "standardizedFamily": ("gbifFamily", "gbif_family"),
    "traitUrn": ("trait_urn", "traitURN", "traitURI"),
    "mappedTraitsUrns": (
        "mappedTraitIDs",
        "mappedTraitsUrn",
        "mappedTraitUrn",
        "mapped_traits_urns",
        "mapped_trait_urns",
    ),
    "modelUri": ("ModelUri", "modelURI", "model_uri"),
    "collectionMethod": ("basisOfRecord", "basis_of_record", "input"),
    "predictionProbability": ("preditionProbability", "prediction_probability", "prediction_prob"),
    "predictionClass": ("prediction_class",),
    "accuracyFamily": ("accuracyExcludingUncertainFamily", "accuracy_excluding_low_certainty_family"),
    "accuracyIncludingUncertainFamily": ("accuracyFamily", "accuracy_including_uncertain_family"),
    "proportionCertaintyFamily": ("proportion_low_certainty_family",),
    "countFamily": ("count_family",),
    "occurrenceID": ("observation_id",),
    "organismID": ("individual_id", "individualID"),
    "locationID": ("siteID", "site_id"),
    "recordedBy": (
        "recorded_by",
        "recordedByID",
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
    ),
    "coordinateUncertaintyInMeters": ("coordinate_uncertainty_meters", "positional_accuracy"),
    "date": ("eventDate",),
}
_TRAITS_CATALOG = None


def parse_bool(value):
    return str(value or "").strip().lower() in {"true", "t", "yes", "y", "1"}


def load_export_column_metadata(columns_path, include_all_columns=False):
    rows = []
    fields = []
    seen = set()

    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clean = {
                key: (value.strip() if isinstance(value, str) else value)
                for key, value in row.items()
            }
            field = clean.get("field", "")
            if not field or field in seen:
                continue
            if not include_all_columns and not parse_bool(clean.get("visible_on_download")):
                continue
            rows.append(clean)
            fields.append(field)
            seen.add(field)

    return rows, fields


def load_export_field_order(columns_path, include_all_columns=False):
    _rows, fields = load_export_column_metadata(columns_path, include_all_columns)
    return fields


def _clean(value):
    return "" if value is None else str(value).strip()


def normalize_key(value):
    return "".join(ch for ch in _clean(value).lower() if ch.isalnum())


def first_value(record, *fields):
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return ""


def fallback_value(record, field):
    return first_value(record, *FIELD_FALLBACKS.get(field, ()))


def normalize_collection_method(value):
    text = _clean(value)
    if not text:
        return text
    return COLLECTION_METHOD_BY_KEY.get(normalize_key(text), text)


def normalize_annotation_method(value):
    text = _clean(value)
    if not text:
        return text
    return ANNOTATION_METHOD_BY_KEY.get(normalize_key(text), text)


def normalize_model_uri(value):
    text = _clean(value)
    if not text:
        return value

    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        return text
    if lower.startswith("doi:"):
        return DOI_RESOLVER_PREFIX + text.split(":", 1)[1].strip()
    if lower.startswith("doi.org/"):
        return DOI_RESOLVER_PREFIX + text.split("/", 1)[1].strip()
    if text.startswith("10."):
        return DOI_RESOLVER_PREFIX + text
    return text


def normalize_export_value(field, value):
    if field in {"modelUri", "ModelUri"}:
        return normalize_model_uri(value)
    if field in {"collectionMethod", "basisOfRecord", "basis_of_record", "input"}:
        return normalize_collection_method(value)
    if field in {"annotationMethod", "annotation_method"}:
        return normalize_annotation_method(value)
    return value


def date_has_month(value):
    text = _clean(value)
    return bool(len(text) >= 7 and text[:4].isdigit() and text[4] == "-" and text[5:7].isdigit())


def is_year_only_herbarium_record(record):
    if normalize_key((record or {}).get("dataSource")) != "herbarium":
        return False

    date_value = _clean((record or {}).get("date") or (record or {}).get("eventDate"))
    if date_has_month(date_value):
        return False
    if date_value and date_value.isdigit() and len(date_value) == 4:
        return True
    return bool(_clean((record or {}).get("year"))) and not date_value


def should_skip_source(record):
    return is_year_only_herbarium_record(record)


def source_family_value(record):
    return first_value(record, "verbatimFamily", "family", "verbatim_family")


def derive_standardized_family(record):
    scientific_name = _clean(record.get("scientificName"))
    if not scientific_name:
        return ""

    if normalize_key(record.get("taxonRank")) == "family":
        return scientific_name

    family = source_family_value(record)
    if family and family.casefold() == scientific_name.casefold():
        return scientific_name

    return family


def traits_catalog():
    global _TRAITS_CATALOG
    if _TRAITS_CATALOG is None:
        _TRAITS_CATALOG = load_traits_catalog(DEFAULT_TRAITS_PATH)
    return _TRAITS_CATALOG


def resolve_trait_record(record):
    trait_urn = first_value(record, "traitUrn", "trait_urn", "traitURN", "traitURI")
    trait = first_value(record, "trait")
    return resolve_trait(traits_catalog(), trait_urn=trait_urn, trait=trait)


def derive_trait_value(record, field):
    trait_record = resolve_trait_record(record)
    if not trait_record:
        return ""
    if field in {"traitUrn", "trait_urn"}:
        return trait_record.get("trait_urn", "")
    if field == "trait":
        return trait_record.get("trait", "")
    if field == "mappedTraits":
        return trait_record.get("mappedTraits", "")
    if field in {"mappedTraitsUrns", "mappedTraitUrn"}:
        return trait_record.get("mappedTraitIDs", "")
    return ""


def derive_annotation_method(record):
    explicit = normalize_annotation_method(first_value(record, "annotationMethod", "annotation_method"))
    if explicit:
        return explicit

    basis_key = normalize_key(first_value(record, "collectionMethod", "basisOfRecord", "basis_of_record", "input"))
    return ANNOTATION_METHOD_BY_BASIS.get(basis_key, "")


def derive_input(record):
    existing = normalize_collection_method(record.get("input"))
    if existing:
        return existing

    source = _clean(record.get("dataSource")).lower()
    method = normalize_annotation_method(first_value(record, "annotationMethod", "annotation_method")).lower()
    basis = normalize_collection_method(first_value(record, "collectionMethod", "basisOfRecord", "basis_of_record")).lower()

    if "herbarium" in source:
        return HERBARIUM_COLLECTION_METHOD
    if "inaturalist" in source or source == "inat" or "inat" in source:
        return LIVE_PLANT_COLLECTION_METHOD
    if method == "human" or basis == HUMAN_COLLECTION_METHOD:
        return HUMAN_COLLECTION_METHOD
    if basis:
        return basis
    return ""


def derive_occurrence_id(record):
    existing = first_value(record, "occurrenceID", "observation_id")
    if existing:
        return existing

    annotation_id = _clean(record.get("annotationID"))
    source = _clean(record.get("dataSource")).lower()
    if annotation_id.startswith("npn:"):
        return annotation_id[4:]
    if annotation_id.isdigit() and "national phenology network" in source:
        return annotation_id
    return ""


def source_record_url(record):
    return source_record_url_for_record(record) or first_value(record, *FIELD_FALLBACKS["sourceRecordUrl"])


def enrich_export_record(record):
    enriched = dict(record or {})
    value = source_record_url(enriched)
    if value:
        enriched["sourceRecordUrl"] = value
    return enriched


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
    record = enrich_export_record(record)
    projected = {}

    for field in field_order:
        if field == "eventDate":
            value = first_value(record, "eventDate", "date")
        elif field == "date":
            value = first_value(record, "date", "eventDate")
        elif field == "sourceRecordUrl":
            value = source_record_url(record)
        elif field == "siteID":
            value = first_value(record, "siteID", "locationID", "site_id")
        elif field == "locationID":
            value = first_value(record, "locationID", "siteID", "site_id")
        elif field == "traitUrn":
            value = first_value(record, "traitUrn", "trait_urn", "traitURN", "traitURI")
            if not value:
                value = derive_trait_value(record, field)
        elif field == "mappedTraitsUrns":
            value = first_value(record, "mappedTraitsUrns", "mappedTraitIDs", "mappedTraitsUrn", "mappedTraitUrn")
            if not value:
                value = derive_trait_value(record, field)
        elif field == "mappedTraitUrn":
            value = first_value(record, "mappedTraitUrn", "mappedTraitsUrns", "mappedTraitIDs")
            if not value:
                value = derive_trait_value(record, field)
        elif field == "mappedTraits":
            value = first_value(record, "mappedTraits")
            if not value:
                value = derive_trait_value(record, field)
        elif field == "annotationMethod":
            value = derive_annotation_method(record)
        elif field == "collectionMethod":
            value = normalize_collection_method(first_value(record, "collectionMethod", "basisOfRecord", "basis_of_record", "input"))
            if not value:
                value = derive_input(record)
        elif field == "input":
            value = derive_input(record)
        elif field == "standardizedFamily":
            value = first_value(record, "standardizedFamily")
            if not value:
                value = fallback_value(record, field)
            if not value:
                value = derive_standardized_family(record)
        elif field == "verbatimFamily":
            value = first_value(record, "verbatimFamily", "family", "verbatim_family")
        elif field == "organismID":
            value = first_value(record, "organismID", "individual_id", "individualID")
        elif field == "occurrenceID":
            value = derive_occurrence_id(record)
        elif field == "accuracyFamily":
            value = fallback_value(record, field)
            if not value:
                value = first_value(record, field)
        elif field == "accuracyIncludingUncertainFamily":
            value = first_value(record, field)
            if not value:
                value = fallback_value(record, field)
        elif field == "verbatimTrait":
            value = normalize_verbatim_trait(first_value(record, "verbatimTrait"), record)
        else:
            value = record.get(field)
            if value in (None, ""):
                value = fallback_value(record, field)
            if value in (None, "") and field in {"trait", "trait_urn"}:
                value = derive_trait_value(record, field)
        projected[field] = normalize_export_value(field, value)

    return projected
