import unittest
from collections import Counter
from datetime import date

from source_citations import (
    acknowledgements_for_data_sources,
    citation_for_data_source,
    citation_markdown,
    citations_for_data_sources,
    source_citation_rows,
)


ACCESS_DATE = date(2026, 9, 10)


class SourceCitationTests(unittest.TestCase):
    def test_known_source_citation_fills_access_year_and_date(self):
        citation = citation_for_data_source("USA National Phenology Network", ACCESS_DATE)

        self.assertIn("USA National Phenology Network. 2026.", citation)
        self.assertIn("Data set accessed Sep 10, 2026 via Phenobase", citation)
        self.assertIn("[Date range of data used]", citation)

    def test_inaturalist_keeps_user_fill_placeholders(self):
        citation = citation_for_data_source("iNaturalist", ACCESS_DATE)

        self.assertIn("[species list]", citation)
        self.assertIn("[geographic area]", citation)
        self.assertIn("[country name]", citation)
        self.assertIn("[date range]", citation)
        self.assertIn("on Sep 10, 2026.", citation)

    def test_citations_are_limited_to_included_sources(self):
        data_sources = Counter(
            {
                "iNaturalist": 3,
                "Budburst": 2,
                "USA National Phenology Network": 0,
            }
        )

        citations = citations_for_data_sources(data_sources, ACCESS_DATE)
        rows = source_citation_rows(data_sources, ACCESS_DATE)

        self.assertEqual(list(citations), ["Budburst", "iNaturalist"])
        self.assertEqual([row["dataSource"] for row in rows], ["Budburst", "iNaturalist"])
        self.assertEqual([row["recordCount"] for row in rows], [2, 3])

    def test_acknowledgements_use_included_sources(self):
        acknowledgements = acknowledgements_for_data_sources(
            Counter(
                {
                    "iNaturalist": 1,
                    "National Ecological Observatory Network (USA)": 1,
                }
            )
        )

        self.assertEqual(len(acknowledgements), 2)
        self.assertIn("iNaturalist", acknowledgements[0])
        self.assertNotIn("Budburst", acknowledgements[0])
        self.assertIn("National Ecological Observatory Network", acknowledgements[1])

    def test_citation_markdown_includes_source_sections_and_acknowledgements(self):
        markdown = citation_markdown(
            Counter(
                {
                    "SeasonWatch India": 4,
                    "PhenoObs": 1,
                }
            ),
            ACCESS_DATE,
        )

        self.assertIn("### SeasonWatch India", markdown)
        self.assertIn("https://doi.org/10.15468/kdtw96", markdown)
        self.assertIn("### PhenoObs", markdown)
        self.assertIn("## Acknowledgements", markdown)
        self.assertIn("PhenObs", markdown)
        self.assertIn("SeasonWatch (India)", markdown)


if __name__ == "__main__":
    unittest.main()
