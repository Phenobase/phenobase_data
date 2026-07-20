import unittest

import download_csv_dump
import rename_es_fields


class FieldRenameTests(unittest.TestCase):
    def test_export_reads_legacy_es_fields_into_new_columns(self):
        source = {
            "gbifFamily": "Fagaceae",
            "basisOfRecord": "Human Observation",
            "accuracyFamily": "0.75",
            "accuracyExcludingUncertainFamily": "0.91",
        }
        row = download_csv_dump.build_csv_row(
            source,
            [
                "standardizedFamily",
                "collectionMethod",
                "accuracyIncludingUncertainFamily",
                "accuracyFamily",
            ],
        )

        self.assertEqual(row["standardizedFamily"], "Fagaceae")
        self.assertEqual(row["collectionMethod"], "Human Observation")
        self.assertEqual(row["accuracyIncludingUncertainFamily"], "0.75")
        self.assertEqual(row["accuracyFamily"], "0.91")

    def test_export_derives_standardized_family_for_family_level_names(self):
        row = download_csv_dump.build_csv_row(
            {
                "scientificName": "Fabaceae",
                "taxonRank": "family",
                "verbatimFamily": "Fabaceae",
            },
            ["standardizedFamily"],
        )
        self.assertEqual(row["standardizedFamily"], "Fabaceae")

    def test_export_derives_standardized_family_when_source_family_matches_name(self):
        row = download_csv_dump.build_csv_row(
            {
                "scientificName": "Rosaceae",
                "verbatimFamily": "Rosaceae",
            },
            ["standardizedFamily"],
        )
        self.assertEqual(row["standardizedFamily"], "Rosaceae")

    def test_rename_query_omits_legacy_accuracy_family_by_default(self):
        body = rename_es_fields.build_update_body()
        script = body["script"]["source"]
        query = body["query"]

        self.assertIn("standardizedFamily", script)
        self.assertIn("collectionMethod", script)
        self.assertIn("accuracyExcludingUncertainFamily", script)
        self.assertNotIn("accuracyIncludingUncertainFamily = oldValue", script)
        self.assertEqual(len(query["bool"]["should"]), 3)

    def test_rename_query_can_include_one_time_legacy_accuracy_family_move(self):
        body = rename_es_fields.build_update_body(include_legacy_accuracy_family=True)
        script = body["script"]["source"]
        query = body["query"]

        self.assertIn("accuracyIncludingUncertainFamily = oldValue", script)
        self.assertEqual(len(query["bool"]["should"]), 4)


if __name__ == "__main__":
    unittest.main()
