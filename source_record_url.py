#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Source-record URL derivation helpers."""

from __future__ import annotations

from urllib.parse import urlencode


NPN_OBSERVATION_URL = "https://services.usanpn.org/npn_portal/observations/getObservationById.json"
NPN_SOURCE_NAMES = {
    "usanationalphenologynetwork",
    "nationalecologicalobservatorynetworkusa",
}
SOURCE_RECORD_URL_FIELDS = (
    "sourceRecordUrl",
    "observedMetadataUrl",
    "observedMetadataURL",
    "observed_metadata_url",
    "observationMetadataUrl",
    "observationMetadatUrl",
)


def norm(value):
    return "" if value is None else str(value).strip()


def normalize_key(value):
    return "".join(ch for ch in norm(value).lower() if ch.isalnum())


def existing_source_record_url(source):
    for field in SOURCE_RECORD_URL_FIELDS:
        value = norm((source or {}).get(field))
        if value:
            return value
    return ""


def is_npn_or_neon_source(source):
    data_source = normalize_key((source or {}).get("dataSource"))
    return (
        data_source in NPN_SOURCE_NAMES
        or "nationalphenologynetwork" in data_source
        or "nationalecologicalobservatorynetwork" in data_source
    )


def build_npn_observation_url(observation_id):
    observation_id = norm(observation_id)
    if observation_id.startswith("npn:"):
        observation_id = observation_id[4:].strip()
    if not observation_id:
        return ""
    query = urlencode(
        {
            "request_src": "PPO",
            "observation_id": observation_id,
            "pretty": "1",
        }
    )
    return f"{NPN_OBSERVATION_URL}?{query}"


def npn_observation_id(source):
    source = source or {}
    source_is_npn = is_npn_or_neon_source(source)

    for field in ("annotationID", "_id"):
        value = norm(source.get(field))
        if value.startswith("npn:"):
            return value[4:].strip()

    if not source_is_npn:
        return ""

    for field in ("occurrenceID", "observation_id", "Observation_ID", "observationID", "annotationID"):
        value = norm(source.get(field))
        if value.startswith("npn:"):
            value = value[4:].strip()
        if value:
            return value

    return ""


def derive_source_record_url(source):
    observation_id = npn_observation_id(source)
    if observation_id:
        return build_npn_observation_url(observation_id)
    return ""


def source_record_url_for_record(source):
    return derive_source_record_url(source) or existing_source_record_url(source)
