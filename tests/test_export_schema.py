import unittest

from download_csv_dump import build_csv_row, load_field_order


class ExportSchemaSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fields = load_field_order("data/columns.csv")

    def test_export_headers_are_curated(self):
        self.assertIn("date", self.fields)
        self.assertIn("sourceRecordUrl", self.fields)
        self.assertIn("locationID", self.fields)
        self.assertIn("traitUrn", self.fields)
        self.assertIn("annotationMethod", self.fields)
        self.assertIn("mappedTraitsUrns", self.fields)
        self.assertIn("collectionMethod", self.fields)

        for field in (
            "eventDate",
            "observedMetadataUrl",
            "siteID",
            "trait_urn",
            "annotation_method",
            "mappedTraitUrn",
            "input",
            "taxonSearch",
            "decadeStart",
            "proportionCertaintyFamily",
            "countFamily",
            "certainty",
            "errorMessage",
            "predictionProbability",
            "preditionProbability",
            "predictionClass",
        ):
            self.assertNotIn(field, self.fields)

    def test_inaturalist_projection(self):
        row = build_csv_row(
            {
                "annotationID": "inat:1",
                "dataSource": "iNaturalist",
                "scientificName": "Acer rubrum",
                "trait": "green leaves present",
                "trait_urn": "PPO:0000001",
                "family": "Sapindaceae",
                "year": 2020,
                "dayOfYear": 123,
                "latitude": 40.1,
                "longitude": -88.2,
                "observedMetadataUrl": "https://www.inaturalist.org/observations/1",
                "annotation_method": "machine",
                "occurrenceID": "1",
                "date": "2020-05-02",
                "verbatimTrait": "green leaves",
                "mappedTraits": ["green leaves present", "plant structure present"],
                "mappedTraitsUrns": ["PPO:0000001", "PPO:0000002"],
            },
            self.fields,
        )

        self.assertEqual(row["traitUrn"], "PPO:0000001")
        self.assertEqual(row["sourceRecordUrl"], "https://www.inaturalist.org/observations/1")
        self.assertEqual(row["annotationMethod"], "machine")
        self.assertEqual(row["date"], "2020-05-02")
        self.assertEqual(row["collectionMethod"], "live plant image")
        self.assertEqual(row["verbatimTrait"], "green leaves = present")
        self.assertEqual(row["mappedTraitsUrns"], "PPO:0000001|PPO:0000002")

    def test_herbarium_projection(self):
        row = build_csv_row(
            {
                "annotationID": "herbarium:1",
                "dataSource": "Herbarium",
                "scientificName": "Quercus alba",
                "trait": "flower present",
                "trait_urn": "PPO:0002331",
                "basisOfRecord": "Preserved Specimen",
                "date": "1950-04-03",
                "verbatimTrait": "flower present",
            },
            self.fields,
        )

        self.assertEqual(row["collectionMethod"], "herbarium specimen image")
        self.assertEqual(row["date"], "1950-04-03")
        self.assertEqual(row["verbatimTrait"], "flower = present")

        plural_row = build_csv_row(
            {
                "annotationID": "herbarium:2",
                "dataSource": "Herbarium",
                "scientificName": "Quercus alba",
                "trait": "flower present",
                "trait_urn": "PPO:0002331",
                "basisOfRecord": "Preserved Specimen",
                "date": "1950-04-03",
                "verbatimTrait": "flowers",
            },
            self.fields,
        )
        self.assertEqual(plural_row["verbatimTrait"], "flowers = present")

    def test_npn_projection(self):
        row = build_csv_row(
            {
                "annotationID": "npn:123",
                "dataSource": "USA National Phenology Network",
                "scientificName": "Acer rubrum",
                "trait": "senescing true leaf absent",
                "trait_urn": "PPO:0002616",
                "year": 2021,
                "site_id": "site-7",
                "individual_id": "plant-9",
                "basisOfRecord": "Human Observation",
                "annotation_method": "in_situ",
                "date": "2021-10-01",
                "verbatimTrait": "Colored leaves (0)",
            },
            self.fields,
        )

        self.assertEqual(row["occurrenceID"], "123")
        self.assertEqual(row["organismID"], "plant-9")
        self.assertEqual(row["locationID"], "site-7")
        self.assertEqual(row["annotationMethod"], "human")
        self.assertEqual(row["date"], "2021-10-01")
        self.assertEqual(row["collectionMethod"], "human observation")
        self.assertEqual(row["verbatimTrait"], "Colored leaves = 0")

        row_without_explicit_method = build_csv_row(
            {
                "annotationID": "npn:456",
                "dataSource": "USA National Phenology Network",
                "scientificName": "Acer rubrum",
                "basisOfRecord": "Human Observation",
                "date": "2021-10-01",
                "verbatimTrait": "Open flowers (0)",
                "trait": "open flower absent",
                "trait_urn": "PPO:0002632",
            },
            self.fields,
        )
        self.assertEqual(row_without_explicit_method["annotationMethod"], "human")


if __name__ == "__main__":
    unittest.main()
