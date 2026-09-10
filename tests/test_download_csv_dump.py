import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

import download_csv_dump


class DownloadCsvDumpTests(unittest.TestCase):
    def test_default_citations_output_uses_output_stem(self):
        self.assertEqual(
            download_csv_dump.default_citations_output("downloads/phenobase_dump.csv"),
            "downloads/phenobase_dump_citations.md",
        )

    def test_write_citations_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "citations.md"

            download_csv_dump.write_citations(
                output,
                Counter({"iNaturalist": 2}),
                date(2026, 9, 10),
            )

            text = output.read_text(encoding="utf-8")
            self.assertIn("# Citations", text)
            self.assertIn("### iNaturalist", text)
            self.assertIn("[species list]", text)
            self.assertIn("Sep 10, 2026", text)


if __name__ == "__main__":
    unittest.main()
