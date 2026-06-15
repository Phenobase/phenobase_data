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

Three-source smoke test with one live record each from iNaturalist, Herbarium, and
USA-NPN:

```bash
python3 build_zenodo_three_source_smoke_test.py
```

## Package Contents

- `phenobase_observations.csv.gz`: compressed CSV export of matching Phenobase records.
- `data_dictionary.csv`: data dictionary generated from `data/columns.csv`.
- `column_metadata.json`: JSON copy of the column metadata.
- `source_summary.csv`: exported record counts by `dataSource`.
- `record_summary.json`: export parameters, row counts, date/year ranges, and missing-link counts.
- `zenodo_metadata.json`: editable Zenodo deposition metadata template.
- `manifest-sha256.txt`: SHA-256 checksums and byte sizes.
- `README.md`: package documentation.

By default the CSV uses the same curated export field set as portal downloads. Internal
fields such as `date`, `observedMetadataUrl`, `locationID`, `trait_urn`, and
`annotation_method` are projected to `eventDate`, `sourceRecordUrl`, `siteID`,
`traitUrn`, and `annotationMethod`; `taxonSearch`, `decadeStart`, and model QA fields
are excluded. Use `--include-all-columns` to include every non-excluded field listed in
`data/columns.csv`.

## Zenodo Review Checklist

- Review `record_summary.json`, especially `missingSourceRecordUrl`, before depositing.
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
