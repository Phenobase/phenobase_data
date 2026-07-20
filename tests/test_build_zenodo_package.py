import csv
import gzip
import json
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

import build_zenodo_package as zenodo


def download_visible_fields(columns_path="data/columns.csv"):
    fields = []
    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("visible_on_download") or "").strip().lower() == "true":
                fields.append((row.get("field") or "").strip())
    return [field for field in fields if field]


def all_column_fields(columns_path="data/columns.csv"):
    fields = []
    with open(columns_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            field = (row.get("field") or "").strip()
            if field:
                fields.append(field)
    return fields


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


if __name__ == "__main__":
    unittest.main()
