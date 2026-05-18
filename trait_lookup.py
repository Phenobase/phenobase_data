#!/usr/bin/env python3
"""Helpers for resolving PPO trait IDs against the current traits.csv file."""

import csv
import os


def _normalize_text(value):
    return (value or "").strip()


def _normalize_label(value):
    return _normalize_text(value).lower()


def load_traits_catalog(path="data/traits.csv"):
    """Return trait lookup tables keyed by PPO ID and by canonical label."""

    catalog = {
        "by_urn": {},
        "by_label": {},
    }

    if not os.path.exists(path):
        return catalog

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trait_urn = _normalize_text(row.get("trait_urn"))
            trait = _normalize_text(row.get("trait"))
            record = {
                "trait_urn": trait_urn,
                "trait": trait,
                "mappedTraitIDs": _normalize_text(row.get("mappedTraitIDs")),
                "mappedTraits": _normalize_text(row.get("mappedTraits")),
            }
            if trait_urn:
                catalog["by_urn"][trait_urn] = record
            if trait:
                catalog["by_label"][_normalize_label(trait)] = record

    return catalog


def resolve_trait(catalog, trait_urn=None, trait=None):
    """Resolve a trait record by PPO ID first, then canonical label."""

    if catalog is None:
        catalog = load_traits_catalog()

    if trait_urn:
        record = catalog["by_urn"].get(_normalize_text(trait_urn))
        if record:
            return record

    if trait:
        return catalog["by_label"].get(_normalize_label(trait))

    return None


def canonical_label_for_urn(trait_urn, catalog=None):
    record = resolve_trait(catalog, trait_urn=trait_urn)
    return record["trait"] if record else ""


def mapped_traits_for_urn(trait_urn, catalog=None):
    record = resolve_trait(catalog, trait_urn=trait_urn)
    if not record:
        return "", ""
    return record["mappedTraitIDs"], record["mappedTraits"]
