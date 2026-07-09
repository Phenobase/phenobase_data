#!/usr/bin/env python3
"""GBIF family-name resolution with a reusable CSV cache."""

from __future__ import annotations

import csv
import os
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_GBIF_CACHE = "downloads/taxonomy_family_compare/gbif_family_cache.csv"
GBIF_MATCH_URL = "https://api.gbif.org/v1/species/match"
CACHE_FIELDS = [
    "scientificName",
    "family",
    "canonicalName",
    "matchedScientificName",
    "status",
    "matchType",
    "confidence",
    "usageKey",
    "error",
]


def normalize_name(value):
    return " ".join(str(value or "").strip().split())


def cache_key(value):
    return normalize_name(value).lower()


class GbifFamilyResolver:
    def __init__(
        self,
        cache_path=DEFAULT_GBIF_CACHE,
        enabled=True,
        timeout=30,
        user_agent="Phenobase-GBIF-family-resolver/1.0",
        pause_seconds=0.0,
    ):
        self.cache_path = cache_path
        self.enabled = enabled
        self.timeout = timeout
        self.user_agent = user_agent
        self.pause_seconds = pause_seconds
        self.cache = {}
        self.cache_fields = list(CACHE_FIELDS)
        self.requests = 0
        self.cache_hits = 0
        self.errors = 0
        self.load_cache()

    def load_cache(self):
        if not self.cache_path or not os.path.exists(self.cache_path):
            return

        with open(self.cache_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                fields = list(reader.fieldnames)
                for field in CACHE_FIELDS:
                    if field not in fields:
                        fields.append(field)
                self.cache_fields = fields
            for row in reader:
                name = row.get("scientificName") or row.get("name")
                key = cache_key(name)
                if key:
                    self.cache[key] = row

    def append_cache_row(self, row):
        if not self.cache_path:
            return

        os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
        file_exists = os.path.exists(self.cache_path) and os.path.getsize(self.cache_path) > 0
        with open(self.cache_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.cache_fields, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow({field: row.get(field, "") for field in self.cache_fields})

    def resolve(self, scientific_name):
        name = normalize_name(scientific_name)
        if not name:
            return {}

        key = cache_key(name)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached

        if not self.enabled:
            row = {"scientificName": name, "family": "", "error": "resolution disabled"}
            self.cache[key] = row
            return row

        if self.pause_seconds > 0:
            time.sleep(self.pause_seconds)

        params = urllib.parse.urlencode({"name": name, "kingdom": "Plantae"})
        request = urllib.request.Request(
            f"{GBIF_MATCH_URL}?{params}",
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        row = {"scientificName": name}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                import json

                data = json.load(response)
            row.update(
                {
                    "family": normalize_name(data.get("family")),
                    "canonicalName": normalize_name(data.get("canonicalName")),
                    "matchedScientificName": normalize_name(data.get("scientificName")),
                    "status": normalize_name(data.get("status")),
                    "matchType": normalize_name(data.get("matchType")),
                    "confidence": data.get("confidence", ""),
                    "usageKey": data.get("usageKey", ""),
                    "error": "",
                }
            )
            self.requests += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            self.errors += 1
            row.update({"family": "", "error": str(exc)})

        self.cache[key] = row
        self.append_cache_row(row)
        return row

    def family_for(self, scientific_name):
        return normalize_name(self.resolve(scientific_name).get("family"))
