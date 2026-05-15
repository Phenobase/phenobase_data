# Phenobase Data Workflow

Rendered traits explorer:

- `https://phenobase.github.io/phenobase_data/traits.html`

Other published Pages:

- Workflow overview: `https://phenobase.github.io/phenobase_data/`
- Published traits CSV: `https://phenobase.github.io/phenobase_data/traits.csv`

## What This Repo Does

This repository supports the Phenobase data pipeline around three core jobs:

1. Rebuild the ontology-derived trait hierarchy in `data/traits.csv`.
2. Load source CSV or TSV datasets into the Elasticsearch index used by Phenobase.
3. Maintain and inspect the live datastore with export and backfill helpers.

The trait reasoning step comes first. The loader depends on `data/traits.csv` to expand each incoming `trait` into the derived `mappedTraits` hierarchy used later for indexing and querying.

## Recommended Order

For a new release, the usual sequence is:

1. Rebuild `data/traits.csv` from the latest PPO ontology.
2. Review the ontology version and diff the regenerated traits file.
3. Prepare the source dataset directory and optional `transform.yaml`.
4. Run a dry ingestion pass with `loader.py --test`.
5. Review `loading_errors.csv` and fix source-data or transform issues.
6. Run the real ingestion.
7. If needed, backfill `decadeStart` on the live index.
8. Optionally export or inspect the live index with the CSV dump helper.

## Reasoning

Reasoning is the first step because it generates the trait lookup consumed during ingestion.

Source of truth for the current workflow:

- PPO GitHub `main`: `https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl`

Run the rebuild from the repo root:

```bash
python3 reasoning/refresh_traits.py
```

Compatibility wrapper:

```bash
./reasoning/get_traits.sh
```

What this rebuild does:

- downloads the current PPO ontology from GitHub `main`
- snapshots the exact ontology used under `reasoning/<version>/ppo.owl`
- regenerates `data/traits.csv`
- publishes `docs/traits.csv` for GitHub Pages
- publishes `docs/traits-data.json` for the static viewer
- writes `reasoning/traits_build_metadata.json`

Current reasoning artifacts:

- [reasoning/refresh_traits.py](/Users/jdeck/IdeaProjects/phenobase_data/reasoning/refresh_traits.py:1)
- [reasoning/traits_build_metadata.json](/Users/jdeck/IdeaProjects/phenobase_data/reasoning/traits_build_metadata.json:1)
- [reasoning/2025-05-05/ppo.owl](/Users/jdeck/IdeaProjects/phenobase_data/reasoning/2025-05-05/ppo.owl:1)
- [reasoning/2026-05-06/ppo.owl](/Users/jdeck/IdeaProjects/phenobase_data/reasoning/2026-05-06/ppo.owl:1)
- [data/traits.csv](/Users/jdeck/IdeaProjects/phenobase_data/data/traits.csv:1)
- [docs/traits.csv](/Users/jdeck/IdeaProjects/phenobase_data/docs/traits.csv:1)
- [docs/traits-data.json](/Users/jdeck/IdeaProjects/phenobase_data/docs/traits-data.json:1)

Quick verification after a rebuild:

```bash
python3 -m json.tool reasoning/traits_build_metadata.json
git diff -- data/traits.csv docs/traits.csv docs/traits-data.json
```

Trait mapping rules used by the rebuild:

- labels ending in ` present` are included
- labels ending in ` absent` are included
- `present` traits map to themselves plus transitive named PPO superclass traits that also end in ` present`
- `absent` traits map only to themselves

## Ingest Procedure

Use this sequence for a new Phenobase data release:

1. Refresh the ontology-driven trait mapping with `python3 reasoning/refresh_traits.py`.
2. Review `reasoning/traits_build_metadata.json` and diff `data/traits.csv`.
3. Place source files in the correct data directory.
4. Add `transform.yaml` if source-specific trait normalization is needed.
5. Run a dry ingestion pass:

```bash
python3 loader.py --mode=<machine|in_situ|herbarium> --test --no-drop-existing <data_dir>
```

6. Review `loading_errors.csv`.
7. Run the real ingestion:

```bash
python3 loader.py --mode=<machine|in_situ|herbarium> --no-drop-existing <data_dir>
```

8. If the live index needs `decadeStart`, run:

```bash
python3 update_decade_start.py --wait
```

## Loading Data

The main loader is [loader.py](/Users/jdeck/IdeaProjects/phenobase_data/loader.py:1).

Supported loading modes:

- `machine`
- `in_situ`
- `herbarium`

Example commands:

```bash
python3 loader.py --mode=machine data/annotations.07.25.2025/ --no-drop-existing --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ data/npn.1956.01.01-2025.08.31/ --no-drop-existing --batch-size 5000 --progress-every 50000
```

Main options:

- `--test`: validate and simulate without writing to Elasticsearch
- `--strict`: reject rows with coercion or validation problems
- `--drop-existing`: recreate the target index before loading
- `--batch-size`: bulk size for indexing
- `--progress-every`: progress logging interval

What the loader relies on:

- `data/columns.csv` for schema and required fields
- `data/traits.csv` for trait-to-`mappedTraits` expansion
- optional dataset-local `transform.yaml` for normalization rules

## Export And Maintenance Utilities

### Download A CSV Dump

Use [download_csv_dump.py](/Users/jdeck/IdeaProjects/phenobase_data/download_csv_dump.py:1) to scroll through the public Phenobase query API and write a local CSV.

```bash
python3 download_csv_dump.py
```

Useful variations:

```bash
python3 download_csv_dump.py --query 'genus:Quercus AND year:[2000 TO 2025]' --output downloads/quercus.csv
python3 download_csv_dump.py --limit 100000
python3 download_csv_dump.py --batch-size 10000 --scroll 1m
python3 download_csv_dump.py --request-timeout 60
```

### Backfill `decadeStart`

Use [update_decade_start.py](/Users/jdeck/IdeaProjects/phenobase_data/update_decade_start.py:1) to add the mapping and backfill `decadeStart` on an existing live index without reloading source files.

```bash
python3 update_decade_start.py
python3 update_decade_start.py --wait
python3 update_decade_start.py --requests-per-second 200
```

## Pages And Shared Outputs

The `docs/` folder is intended for GitHub Pages publication and for quick sharing with collaborators.

Published outputs:

- [docs/index.html](/Users/jdeck/IdeaProjects/phenobase_data/docs/index.html:1): workflow overview page
- [docs/traits.html](/Users/jdeck/IdeaProjects/phenobase_data/docs/traits.html:1): rendered trait explorer
- [docs/traits.csv](/Users/jdeck/IdeaProjects/phenobase_data/docs/traits.csv:1): published CSV copy
- [docs/traits-data.json](/Users/jdeck/IdeaProjects/phenobase_data/docs/traits-data.json:1): viewer payload

## Core Files

- [data/traits.csv](/Users/jdeck/IdeaProjects/phenobase_data/data/traits.csv:1): ontology-derived trait mapping
- [data/columns.csv](/Users/jdeck/IdeaProjects/phenobase_data/data/columns.csv:1): field definitions and schema metadata
- [loader.py](/Users/jdeck/IdeaProjects/phenobase_data/loader.py:1): ingestion driver
- [reasoning/refresh_traits.py](/Users/jdeck/IdeaProjects/phenobase_data/reasoning/refresh_traits.py:1): reasoning rebuild driver
- [download_csv_dump.py](/Users/jdeck/IdeaProjects/phenobase_data/download_csv_dump.py:1): API export helper
- [update_decade_start.py](/Users/jdeck/IdeaProjects/phenobase_data/update_decade_start.py:1): live index backfill helper

## Requirements

- Python 3.8+
- Java 11+ is available locally, though the current trait rebuild script uses only the Python standard library
- Elasticsearch reachable for ingestion or maintenance commands

## Author

PhenoBase Project | Biocode, LLC
