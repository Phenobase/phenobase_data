# Data Loading Notes

## Source Linkback URLs

Prepared ingest CSVs should include `observedMetadataUrl` whenever the source can provide a stable linkback target. The loader normalizes that source-side field to the version2 public field `sourceRecordUrl`.

- Budburst uses the public report page URL: `https://budburst.org/data/{report_id}`.
- NPN uses the official USA-NPN `getObservations.json` API filtered to the source
  observation date, individual ID, and phenophase ID.
- PhenoObs uses the annual iDiv dataset DOI because the raw data do not expose public per-observation pages.
- SeasonWatch India uses direct GBIF occurrence pages by default: `https://www.gbif.org/occurrence/{gbif_key}`.

## SeasonWatch India GBIF Links

The SeasonWatch India DwC-A contains source `occurrenceID` values such as `NCF_India:SeasonWatch:252750`, but it does not include the GBIF occurrence key needed for direct GBIF record pages. The fetcher resolves those keys through the GBIF occurrence API and writes a local cache:

```text
downloads/seasonwatchindia/seasonwatchindia_gbif_keys.csv
```

Normal generation from the repository root:

```bash
python3 downloads/seasonwatchindia/fetch_seasonwatchindia.py \
  --output downloads/seasonwatchindia/ingest/seasonwatchindia_observations.csv
```

The command above writes direct GBIF links by default. For example:

```text
https://www.gbif.org/occurrence/4943546900
```

If GBIF key resolution needs to be skipped, use:

```bash
python3 downloads/seasonwatchindia/fetch_seasonwatchindia.py \
  --output downloads/seasonwatchindia/ingest/seasonwatchindia_observations.csv \
  --no-gbif-direct-links
```

That fallback writes GBIF occurrence search URLs instead of direct occurrence pages.

## Reloading One Source In Place

Use the loader's source-record drop when regenerating a complete source dataset for the
current Elasticsearch index:

```bash
python3 loader.py --mode=in_situ --drop-source-records downloads/phenoObs/ingest \
  --batch-size 5000 \
  --progress-every 50000
```

`--drop-source-records` scans the input CSVs for `dataSource` values, starts an
Elasticsearch delete-by-query task for matching records in `phenobase2`, waits for that
task to finish, and then indexes the regenerated rows. This replaces the separate curl
delete step while preserving records from other sources in the same index.

Dry runs should still use `--test --no-drop-existing`, because test mode never deletes
or indexes records.
