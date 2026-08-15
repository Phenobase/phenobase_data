import csv
import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

import build_zenodo_package as zenodo


def download_visible_fields(columns_path="data/columns.csv"):
    return [row["field"] for row in download_visible_column_rows(columns_path)]


def download_visible_column_rows(columns_path="data/columns.csv"):
    rows = []
    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clean = {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
            if (clean.get("visible_on_download") or "").strip().lower() == "true":
                rows.append(clean)
    return [row for row in rows if row.get("field")]


def all_column_fields(columns_path="data/columns.csv"):
    fields = []
    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            field = (row.get("field") or "").strip()
            if field:
                fields.append(field)
    return fields


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_package_against_columns(testcase, package_dir, columns_path="data/columns.csv"):
    package_dir = Path(package_dir)
    expected_rows = download_visible_column_rows(columns_path)
    expected_fields = [row["field"] for row in expected_rows]
    required_files = {
        "phenobase_observations.csv.gz",
        "data_dictionary.csv",
        "column_metadata.json",
        "source_summary.csv",
        "record_summary.json",
        "zenodo_metadata.json",
        "manifest-sha256.txt",
        "README.md",
    }

    testcase.assertTrue(package_dir.is_dir(), f"Missing package directory: {package_dir}")
    testcase.assertTrue(required_files.issubset({path.name for path in package_dir.iterdir()}))

    with gzip.open(package_dir / "phenobase_observations.csv.gz", "rt", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        testcase.assertEqual(next(reader), expected_fields)

    with open(package_dir / "data_dictionary.csv", newline="", encoding="utf-8") as fh:
        testcase.assertEqual(list(csv.DictReader(fh)), expected_rows)

    with open(package_dir / "column_metadata.json", encoding="utf-8") as fh:
        testcase.assertEqual(json.load(fh), expected_rows)

    with open(package_dir / "record_summary.json", encoding="utf-8") as fh:
        summary = json.load(fh)
    testcase.assertEqual(summary["fieldVisibility"], "visible_on_download")
    testcase.assertEqual(summary["fieldCount"], len(expected_fields))
    testcase.assertEqual(summary["fields"], expected_fields)

    with open(package_dir / "source_summary.csv", newline="", encoding="utf-8") as fh:
        source_summary = {
            row["dataSource"]: int(row["recordCount"])
            for row in csv.DictReader(fh)
        }
    testcase.assertEqual(source_summary, summary["sourceCounts"])

    manifest_entries = {}
    with open(package_dir / "manifest-sha256.txt", encoding="utf-8") as fh:
        for line in fh:
            digest, size, rel = line.rstrip("\n").split("  ", 2)
            manifest_entries[rel] = (digest, int(size))
    expected_manifest_files = sorted(
        path.name for path in package_dir.iterdir()
        if path.is_file() and path.name != "manifest-sha256.txt"
    )
    testcase.assertEqual(sorted(manifest_entries), expected_manifest_files)
    for rel, (digest, size) in manifest_entries.items():
        path = package_dir / rel
        testcase.assertEqual(digest, sha256_file(path))
        testcase.assertEqual(size, path.stat().st_size)


class ZenodoPackageTests(unittest.TestCase):
    def test_default_columns_are_download_visible_order(self):
        column_rows, fields = zenodo.load_column_metadata("data/columns.csv")
        expected = download_visible_fields()

        self.assertEqual(fields, expected)
        self.assertEqual([row["field"] for row in column_rows], expected)
        self.assertIn("standardizedFamily", fields)
        self.assertIn("collectionMethod", fields)
        self.assertIn("accuracyFamily", fields)
        self.assertNotIn("taxonSearch", fields)
        self.assertNotIn("gbifFamily", fields)
        self.assertNotIn("basisOfRecord", fields)
        self.assertNotIn("accuracyIncludingUncertainFamily", fields)
        self.assertNotIn("predictionProbability", fields)
        self.assertNotIn("errorMessage", fields)

    def test_include_all_columns_preserves_columns_csv_order(self):
        column_rows, fields = zenodo.load_column_metadata("data/columns.csv", include_all_columns=True)
        expected = all_column_fields()

        self.assertEqual(fields, expected)
        self.assertEqual([row["field"] for row in column_rows], expected)
        self.assertIn("predictionProbability", fields)
        self.assertIn("errorMessage", fields)

    def test_sampled_package_uses_download_visible_header_and_dictionary(self):
        original_argv = sys.argv
        original_collect = zenodo.collect_live_dataset_counts
        original_fetch = zenodo.fetch_datasource_sample

        def fake_collect(_args):
            return OrderedDict(
                [
                    ("Source A", 12),
                    ("Source B", 34),
                ]
            )

        def fake_fetch(_args, data_source):
            source = {
                "annotationID": f"{data_source}-1",
                "scientificName": "Quercus agrifolia",
                "verbatimFamily": "Fagaceae",
                "standardizedFamily": "Fagaceae",
                "taxonSearch": ["Fagaceae", "Quercus", "Quercus agrifolia"],
                "trait": "open flower present",
                "traitUrn": "PPO:0002333",
                "mappedTraits": ["open flower present", "flower present"],
                "mappedTraitsUrns": ["PPO:0002333", "PPO:0002313"],
                "date": "2020-05-01",
                "year": 2020,
                "dayOfYear": 122,
                "dataSource": data_source,
                "sourceRecordUrl": f"https://example.org/{data_source}",
            }
            return [source], 1, 0, source["annotationID"], source["annotationID"]

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zenodo.collect_live_dataset_counts = fake_collect
                zenodo.fetch_datasource_sample = fake_fetch
                sys.argv = [
                    "build_zenodo_package.py",
                    "--sample-per-datasource",
                    "1",
                    "--output-dir",
                    tmpdir,
                    "--package-name",
                    "phenobase-zenodo-test",
                    "--skip-zip",
                ]

                self.assertEqual(zenodo.main(), 0)

                package_dir = Path(tmpdir) / "phenobase-zenodo-test"
                expected_fields = download_visible_fields()

                with gzip.open(package_dir / "phenobase_observations.csv.gz", "rt", newline="", encoding="utf-8") as fh:
                    reader = csv.reader(fh)
                    self.assertEqual(next(reader), expected_fields)
                    rows = list(reader)
                    self.assertEqual(len(rows), 2)

                with open(package_dir / "data_dictionary.csv", newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    self.assertEqual([row["field"] for row in reader], expected_fields)

                validate_package_against_columns(self, package_dir)

                with open(package_dir / "record_summary.json", encoding="utf-8") as fh:
                    summary = json.load(fh)
                self.assertEqual(summary["fieldVisibility"], "visible_on_download")
                self.assertEqual(summary["fields"], expected_fields)
                self.assertEqual(summary["sourceCounts"], {"Source A": 1, "Source B": 1})
                self.assertFalse((Path(tmpdir) / "phenobase-zenodo-test.zip").exists())
        finally:
            sys.argv = original_argv
            zenodo.collect_live_dataset_counts = original_collect
            zenodo.fetch_datasource_sample = original_fetch

    @unittest.skipUnless(
        os.environ.get("PHENOBASE_ZENODO_PACKAGE_DIR"),
        "Set PHENOBASE_ZENODO_PACKAGE_DIR to validate a generated Zenodo package.",
    )
    def test_generated_package_files_match_columns_csv(self):
        validate_package_against_columns(self, os.environ["PHENOBASE_ZENODO_PACKAGE_DIR"])


if __name__ == "__main__":
    unittest.main()
