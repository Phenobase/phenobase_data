# Zenodo Package Workflow

Use `build_zenodo_package.py` to export Phenobase records and create a package suitable
for public sharing and Zenodo submission.

## Package Command

Production run from the repository root:

```bash
python3 build_zenodo_package.py \
  --package-name phenobase-zenodo-2026-06-04 \
  --version 2026-06-04 \
  --publication-date 2026-06-04 \
  --creator "Phenobase Project" \
  --batch-size 10000 \
  --scroll 5m \
  --request-timeout 120
```

Small test run:

```bash
python3 build_zenodo_package.py --limit 100 --package-name phenobase-zenodo-smoke-test
```

All-datasets example package with 20 records per `dataSource` and exact live source counts:

```bash
python3 build_zenodo_package.py \
  --sample-per-datasource 20 \
  --package-name phenobase-zenodo-example-all-datasets-2026-07-06 \
  --version 2026-07-06 \
  --publication-date 2026-07-06 \
  --request-timeout 120
```

Trait-coverage example package with up to 20 flower, fruit, and leaf records per `dataSource`:

```bash
python3 build_zenodo_package.py \
  --sample-per-datasource-trait-category 20 \
  --package-name phenobase-zenodo-trait-coverage-example-2026-08-15 \
  --version 2026-08-15 \
  --publication-date 2026-08-15 \
  --request-timeout 120
```

## Package Contents

- `phenobase_observations.csv.gz`: compressed CSV export of matching Phenobase records.
- `data_dictionary.csv`: data dictionary generated from `data/columns.csv`.
- `column_metadata.json`: JSON copy of the column metadata.
- `source_summary.csv`: exported record counts by `dataSource`.
- `source_citations.csv`: citation text for each exported `dataSource`.
- `CITATION.md`: human-readable source citations and acknowledgements.
- `live_dataset_counts.csv`: optional exact live and included counts by `dataSource` for `--sample-per-datasource`.
- `trait_category_sample_counts.csv`: optional exact live and included counts by `dataSource` and trait category for `--sample-per-datasource-trait-category`.
- `record_summary.json`: export parameters, row counts, date/year ranges, source citations, missing-link counts, and skipped year-only herbarium records.
- `zenodo_metadata.json`: editable Zenodo deposition metadata template.
- `manifest-sha256.txt`: SHA-256 checksums and byte sizes.
- `README.md`: package documentation.

By default the CSV includes fields marked `visible_on_download=TRUE` in `data/columns.csv`.
Use `--include-all-columns` to include every field listed in `data/columns.csv`.

## Zenodo Review Checklist

- Review `record_summary.json`, especially `missingSourceRecordUrl`, before depositing.
- Review `CITATION.md` and fill bracketed placeholders such as `[Date range of data used]` before publication when needed.
- Review and edit `zenodo_metadata.json`; creators, affiliations, funders, related identifiers,
  community, and license must be confirmed by the submitting team.
- Keep the number of uploaded files low. The generated ZIP is intended to be the main Zenodo upload.
- Use `README.md`, `data_dictionary.csv`, and `manifest-sha256.txt` inside the package as public documentation.
- After Zenodo publication, uploaded files are immutable. Publish a new Zenodo version when files change.

## Zenodo Notes

Zenodo records include metadata, files, and a DOI. Metadata remains editable after publication,
but files and persistent identifiers do not. Zenodo currently lists a standard record limit of
50GB and 100 files; for larger one-time deposits, contact Zenodo about quota increases before
submission.
