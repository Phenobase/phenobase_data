import csv
import unittest


DATASOURCE_PATH = "data/datasource.csv"
DEFINITIONS_PATH = "data/datasource_definitions.csv"
EXPECTED_COLUMNS = [
    "dataSource",
    "displayName",
    "providerName",
    "sourceType",
    "sourceUrl",
    "sourceIdentifier",
    "sourceVersion",
    "licenseId",
    "licenseName",
    "licenseUrl",
    "citationText",
    "dataUseNotes",
    "phenobaseProcessingSummary",
    "citationSourceUrl",
]
EXPECTED_DEFINITION_COLUMNS = ["field", "definition"]
LIVE_DATA_SOURCES = {
    "Budburst",
    "Herbarium",
    "National Ecological Observatory Network (USA)",
    "PhenoObs",
    "SeasonWatch (India)",
    "USA National Phenology Network",
    "iNaturalist",
}


def load_datasource_rows(path=DATASOURCE_PATH):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, [
            {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
            for row in reader
        ]


def load_definition_rows(path=DEFINITIONS_PATH):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, [
            {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
            for row in reader
        ]


class DatasourceMetadataTests(unittest.TestCase):
    def test_datasource_csv_has_expected_columns(self):
        fieldnames, _rows = load_datasource_rows()

        self.assertEqual(fieldnames, EXPECTED_COLUMNS)
        self.assertNotIn("sourceIdentifierScheme", fieldnames)
        self.assertNotIn("lastReviewedDate", fieldnames)
        self.assertNotIn("reviewStatus", fieldnames)

    def test_datasource_rows_cover_live_sources(self):
        _fieldnames, rows = load_datasource_rows()
        data_sources = {row["dataSource"] for row in rows}

        self.assertEqual(data_sources, LIVE_DATA_SOURCES)

    def test_datasource_join_keys_are_unique(self):
        _fieldnames, rows = load_datasource_rows()
        data_sources = [row["dataSource"] for row in rows]

        self.assertEqual(len(data_sources), len(set(data_sources)))

    def test_required_public_metadata_is_present(self):
        _fieldnames, rows = load_datasource_rows()

        for row in rows:
            with self.subTest(dataSource=row["dataSource"]):
                self.assertTrue(row["displayName"])
                self.assertTrue(row["providerName"])
                self.assertTrue(row["sourceType"])
                self.assertTrue(row["licenseId"])
                self.assertTrue(row["licenseName"])
                self.assertTrue(row["citationText"] or row["dataUseNotes"])
                self.assertTrue(row["phenobaseProcessingSummary"])

    def test_required_citation_text_is_present(self):
        _fieldnames, rows = load_datasource_rows()

        for row in rows:
            with self.subTest(dataSource=row["dataSource"]):
                self.assertTrue(row["citationText"])
                self.assertNotIn("Confirm whether", row["citationText"])

    def test_datasource_definitions_cover_every_datasource_column(self):
        datasource_fieldnames, _rows = load_datasource_rows()
        definition_fieldnames, definition_rows = load_definition_rows()

        self.assertEqual(definition_fieldnames, EXPECTED_DEFINITION_COLUMNS)
        self.assertEqual(
            [row["field"] for row in definition_rows],
            datasource_fieldnames,
        )
        for row in definition_rows:
            with self.subTest(field=row["field"]):
                self.assertTrue(row["definition"])


if __name__ == "__main__":
    unittest.main()
