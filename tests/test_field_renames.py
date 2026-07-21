import unittest

import backfill_version2_es
import download_csv_dump
import loader
import rename_es_fields
import source_record_url


class FakeGbifResolver:
    def __init__(self, families):
        self.families = families
        self.calls = []

    def family_for(self, name):
        self.calls.append(name)
        return self.families.get(name, "")


class FieldRenameTests(unittest.TestCase):
    def test_export_reads_legacy_es_fields_into_new_columns(self):
        source = {
            "gbifFamily": "Fagaceae",
            "basisOfRecord": "Human Observation",
            "accuracyFamily": "0.75",
            "accuracyExcludingUncertainFamily": "0.91",
            "observedby_person_id": "41422",
        }
        row = download_csv_dump.build_csv_row(
            source,
            [
                "standardizedFamily",
                "collectionMethod",
                "accuracyIncludingUncertainFamily",
                "accuracyFamily",
                "recordedBy",
            ],
        )

        self.assertEqual(row["standardizedFamily"], "Fagaceae")
        self.assertEqual(row["collectionMethod"], "Human Observation")
        self.assertEqual(row["accuracyIncludingUncertainFamily"], "0.75")
        self.assertEqual(row["accuracyFamily"], "0.91")
        self.assertEqual(row["recordedBy"], "41422")

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

    def test_export_falls_back_to_verbatim_family(self):
        row = download_csv_dump.build_csv_row(
            {
                "scientificName": "Brunia nodiflora L.",
                "verbatimFamily": "Bruniaceae",
            },
            ["standardizedFamily"],
        )
        self.assertEqual(row["standardizedFamily"], "Bruniaceae")

    def test_backfill_tries_genus_before_source_family_fallback(self):
        resolver = FakeGbifResolver({"Brunia": "Bruniaceae"})
        updates = backfill_version2_es.derive_updates(
            {
                "scientificName": "Brunia nodiflora L.",
                "genus": "Brunia",
                "verbatimFamily": "Source family",
            },
            {"by_urn": {}, "by_label": {}},
            resolver,
        )

        self.assertEqual(updates["standardizedFamily"], "Bruniaceae")
        self.assertEqual(resolver.calls, ["Brunia nodiflora L.", "Brunia"])

    def test_backfill_falls_back_to_verbatim_family_when_authority_lookup_is_blank(self):
        resolver = FakeGbifResolver({})
        updates = backfill_version2_es.derive_updates(
            {
                "scientificName": "Brunia nodiflora L.",
                "genus": "Brunia",
                "verbatimFamily": "Bruniaceae",
            },
            {"by_urn": {}, "by_label": {}},
            resolver,
        )

        self.assertEqual(updates["standardizedFamily"], "Bruniaceae")
        self.assertEqual(resolver.calls, ["Brunia nodiflora L.", "Brunia"])

    def test_backfill_can_filter_to_standardized_family_only(self):
        self.assertEqual(
            backfill_version2_es.filter_updates(
                {
                    "standardizedFamily": "Bruniaceae",
                    "accuracyIncludingUncertainFamily": "0.75",
                },
                only_standardized_family=True,
            ),
            {"standardizedFamily": "Bruniaceae"},
        )

    def test_backfill_can_filter_to_herbarium_collection_method_only(self):
        self.assertEqual(
            backfill_version2_es.filter_updates(
                {
                    "collectionMethod": "Preserved Specimen",
                    "annotationMethod": "machine",
                },
                only_herbarium_collection_method=True,
            ),
            {"collectionMethod": "Preserved Specimen"},
        )

    def test_backfill_can_filter_to_source_record_url_only(self):
        self.assertEqual(
            backfill_version2_es.filter_updates(
                {
                    "sourceRecordUrl": "https://example.org/source",
                    "collectionMethod": "Human Observation",
                },
                only_source_record_url=True,
            ),
            {"sourceRecordUrl": "https://example.org/source"},
        )

    def test_backfill_forces_herbarium_collection_method(self):
        updates = backfill_version2_es.derive_updates(
            {
                "dataSource": "Herbarium",
                "collectionMethod": "PreservedSpecimen",
            },
            {"by_urn": {}, "by_label": {}},
            FakeGbifResolver({}),
        )

        self.assertEqual(updates["collectionMethod"], "Preserved Specimen")
        self.assertEqual(updates["annotationMethod"], "machine")

    def test_loader_forces_herbarium_collection_method(self):
        es_loader = loader.ESLoader.__new__(loader.ESLoader)
        es_loader.mode = "herbarium"

        row = es_loader.normalize_row_fields(
            {
                "dataSource": "Anything",
                "basisOfRecord": "PreservedSpecimen",
            }
        )

        self.assertEqual(row["dataSource"], "Anything")
        self.assertEqual(row["collectionMethod"], "Preserved Specimen")
        self.assertEqual(row["annotationMethod"], "machine")

    def test_source_record_url_uses_npn_annotation_id(self):
        self.assertEqual(
            source_record_url.source_record_url_for_record(
                {
                    "dataSource": "USA National Phenology Network",
                    "annotationID": "npn:335790",
                }
            ),
            "https://services.usanpn.org/npn_portal/observations/getObservationById.json?request_src=PPO&observation_id=335790&pretty=1",
        )

    def test_source_record_url_uses_neon_occurrence_id(self):
        self.assertEqual(
            source_record_url.source_record_url_for_record(
                {
                    "dataSource": "National Ecological Observatory Network (USA)",
                    "occurrenceID": "45570664",
                }
            ),
            "https://services.usanpn.org/npn_portal/observations/getObservationById.json?request_src=PPO&observation_id=45570664&pretty=1",
        )

    def test_loader_derives_npn_source_record_url(self):
        es_loader = loader.ESLoader.__new__(loader.ESLoader)
        es_loader.mode = "in_situ"

        row = es_loader.normalize_row_fields(
            {
                "dataSource": "USA National Phenology Network",
                "annotationID": "npn:335790",
            }
        )

        self.assertEqual(
            row["sourceRecordUrl"],
            "https://services.usanpn.org/npn_portal/observations/getObservationById.json?request_src=PPO&observation_id=335790&pretty=1",
        )

    def test_backfill_derives_npn_source_record_url(self):
        updates = backfill_version2_es.derive_updates(
            {
                "dataSource": "USA National Phenology Network",
                "annotationID": "npn:335790",
            },
            {"by_urn": {}, "by_label": {}},
            FakeGbifResolver({}),
        )

        self.assertEqual(
            updates["sourceRecordUrl"],
            "https://services.usanpn.org/npn_portal/observations/getObservationById.json?request_src=PPO&observation_id=335790&pretty=1",
        )

    def test_export_derives_npn_source_record_url(self):
        row = download_csv_dump.build_csv_row(
            {
                "dataSource": "USA National Phenology Network",
                "annotationID": "npn:335790",
            },
            ["sourceRecordUrl"],
        )

        self.assertEqual(
            row["sourceRecordUrl"],
            "https://services.usanpn.org/npn_portal/observations/getObservationById.json?request_src=PPO&observation_id=335790&pretty=1",
        )

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
