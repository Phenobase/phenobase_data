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
    def inaturalist_transform(self):
        yaml_rules = {
            "fields": {
                "verbatimTrait": {
                    "transforms": [
                        {
                            "op": "map",
                            "values": {"flower": "open flowers"},
                        }
                    ]
                }
            },
            "trait_mappings": {
                "flower present": "open flower present",
                "green leaves present": "non-senescing unfolded true leaf present",
                "breaking buds present": "breaking vegetative bud present",
                "colored leaves present": "senescing true leaf present",
                "fruit present": "simple fruit or compound fruit present",
            },
        }
        original_load_yaml_mapping = loader.load_yaml_mapping
        try:
            loader.load_yaml_mapping = lambda _path: yaml_rules
            return loader.make_row_transformer("downloads/iNaturalist/ingest/transform.yaml")
        finally:
            loader.load_yaml_mapping = original_load_yaml_mapping

    def test_export_reads_legacy_es_fields_into_new_columns(self):
        source = {
            "gbifFamily": "Fagaceae",
            "basisOfRecord": "Human Observation",
            "annotation_method": "in_situ",
            "accuracyFamily": "0.75",
            "accuracyExcludingUncertainFamily": "0.91",
            "observedby_person_id": "41422",
        }
        row = download_csv_dump.build_csv_row(
            source,
            [
                "standardizedFamily",
                "collectionMethod",
                "annotationMethod",
                "accuracyIncludingUncertainFamily",
                "accuracyFamily",
                "recordedBy",
            ],
        )

        self.assertEqual(row["standardizedFamily"], "Fagaceae")
        self.assertEqual(row["collectionMethod"], "human observation")
        self.assertEqual(row["annotationMethod"], "human")
        self.assertEqual(row["accuracyIncludingUncertainFamily"], "0.75")
        self.assertEqual(row["accuracyFamily"], "0.91")
        self.assertEqual(row["recordedBy"], "41422")

    def test_export_normalizes_collection_method_values(self):
        cases = [
            ({"basisOfRecord": "HumanObservation"}, "human observation"),
            ({"collectionMethod": "Preserved Specimen"}, "herbarium specimen image"),
            ({"collectionMethod": "MachineObservation"}, "live plant image"),
        ]

        for source, expected in cases:
            with self.subTest(source=source):
                row = download_csv_dump.build_csv_row(source, ["collectionMethod"])
                self.assertEqual(row["collectionMethod"], expected)

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
                    "collectionMethod": "herbarium specimen image",
                    "annotationMethod": "machine",
                },
                only_herbarium_collection_method=True,
            ),
            {"collectionMethod": "herbarium specimen image"},
        )

    def test_backfill_can_filter_to_source_record_url_only(self):
        self.assertEqual(
            backfill_version2_es.filter_updates(
                {
                    "sourceRecordUrl": "https://example.org/source",
                    "collectionMethod": "human observation",
                },
                only_source_record_url=True,
            ),
            {"sourceRecordUrl": "https://example.org/source"},
        )

    def test_backfill_normalizes_legacy_collection_and_annotation_methods(self):
        updates = backfill_version2_es.derive_updates(
            {
                "collectionMethod": "Human Observation",
                "annotationMethod": "in_situ",
            },
            {"by_urn": {}, "by_label": {}},
            FakeGbifResolver({}),
        )

        self.assertEqual(updates["collectionMethod"], "human observation")
        self.assertEqual(updates["annotationMethod"], "human")

    def test_backfill_forces_herbarium_collection_method(self):
        updates = backfill_version2_es.derive_updates(
            {
                "dataSource": "Herbarium",
                "collectionMethod": "PreservedSpecimen",
            },
            {"by_urn": {}, "by_label": {}},
            FakeGbifResolver({}),
        )

        self.assertEqual(updates["collectionMethod"], "herbarium specimen image")
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
        self.assertEqual(row["collectionMethod"], "herbarium specimen image")
        self.assertEqual(row["annotationMethod"], "machine")

    def test_inaturalist_transform_yaml_contains_open_flower_mapping(self):
        with open("downloads/iNaturalist/ingest/transform.yaml", encoding="utf-8") as fh:
            transform_yaml = fh.read()

        self.assertIn("verbatimTrait:", transform_yaml)
        self.assertIn("flower: open flowers", transform_yaml)
        self.assertIn("flower present: open flower present", transform_yaml)

    def test_inaturalist_transform_maps_flower_to_open_flower(self):
        transform = self.inaturalist_transform()
        es_loader = loader.ESLoader.__new__(loader.ESLoader)
        es_loader.system_fields = set()
        es_loader.traits_by_urn = loader.traits_catalog["by_urn"]
        es_loader.traits_by_label = loader.traits_catalog["by_label"]

        row = transform(
            {
                "dataSource": "iNaturalist",
                "annotationID": "inat-flower-1",
                "trait": "flower present",
                "verbatimTrait": "flower",
            }
        )
        errors = []
        es_loader.assign_system_fields(row, errors)

        self.assertEqual(errors, [])
        self.assertEqual(row["verbatimTrait"], "open flowers")
        self.assertEqual(row["trait"], "open flower present")
        self.assertEqual(row["traitUrn"], "PPO:0002333")
        self.assertEqual(
            row["mappedTraits"],
            [
                "open flower present",
                "non-senesced flower present",
                "flower present",
                "reproductive shoot system present",
                "reproductive structure present",
                "plant structure present",
            ],
        )
        self.assertEqual(
            row["mappedTraitsUrns"],
            [
                "PPO:0002333",
                "PPO:0002331",
                "PPO:0002330",
                "PPO:0002324",
                "PPO:0002323",
                "PPO:0002300",
            ],
        )

    def test_inaturalist_transform_preserves_non_flower_verbatim_traits(self):
        transform = self.inaturalist_transform()

        fruit = transform(
            {
                "dataSource": "iNaturalist",
                "annotationID": "inat-fruit-1",
                "trait": "fruit present",
                "verbatimTrait": "fruit",
            }
        )
        leaves = transform(
            {
                "dataSource": "iNaturalist",
                "annotationID": "inat-leaves-1",
                "trait": "green leaves present",
                "verbatimTrait": "green leaves",
            }
        )

        self.assertEqual(fruit["trait"], "simple fruit or compound fruit present")
        self.assertEqual(fruit["verbatimTrait"], "fruit")
        self.assertEqual(leaves["trait"], "non-senescing unfolded true leaf present")
        self.assertEqual(leaves["verbatimTrait"], "green leaves")

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
