# Phenobase Data Workflow

## User Guide

Phenobase data loading prepares source observation releases for indexing in the Phenobase Elasticsearch datastore. The workflow begins by rebuilding the ontology-derived trait hierarchy, then uses that trait mapping during dataset normalization, validation, and loading so incoming observations can be indexed with consistent `trait_urn`, `trait`, and `mappedTraits` values. For the current published trait outputs, use the [traits viewer](https://phenobase.github.io/phenobase_data/traits.html) and the [published `traits.csv`](https://phenobase.github.io/phenobase_data/traits.csv).

- [View Traits](https://phenobase.github.io/phenobase_data/traits.html)

## Technical Details And Implementation Guide

### What This Repo Does

This repository supports the Phenobase data pipeline around three core jobs:

1. Rebuild the ontology-derived trait hierarchy in `data/traits.csv`.
2. Load source CSV datasets into the Elasticsearch index used by Phenobase.
3. Maintain and inspect the live datastore with export and backfill helpers.

The trait reasoning step comes first. The loader depends on `data/traits.csv` to resolve each incoming `trait_urn` into the current canonical `trait` label and the derived `mappedTraits` hierarchy used later for indexing and querying.

### Reasoning

Reasoning is the first step because it generates the trait lookup consumed during ingestion.

Source of truth for the current workflow:

- PPO GitHub `main`: `https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl`

For repeatability, this repo supports two ways to regenerate the trait mapping:

- the default code method, which writes the canonical [data/traits.csv](data/traits.csv)
- a separate ROBOT/SPARQL method, which writes [reasoning/robot/traits.csv](reasoning/robot/traits.csv) and a comparison report against the canonical file

#### Method 1: Default Code Method

Run the canonical rebuild from the repo root:

```bash
python3 reasoning/refresh_traits.py
```

Compatibility wrapper:

```bash
./reasoning/get_traits.sh
```

This method:

- downloads the current PPO ontology from GitHub `main`
- snapshots the exact ontology used under `reasoning/<version>/ppo.owl`
- regenerates `data/traits.csv`
- publishes `docs/traits.csv` for GitHub Pages
- publishes `docs/traits-data.json` for the static viewer
- writes `reasoning/traits_build_metadata.json`

#### Method 2: ROBOT/SPARQL Method

The SPARQL query is kept in its own file for repeatability:

- [reasoning/robot/traits_pairs.sparql](reasoning/robot/traits_pairs.sparql)

The direct ROBOT query command is:

```bash
../robot/robot query \
  --input reasoning/2026-05-06/ppo.owl \
  --query reasoning/robot/traits_pairs.sparql reasoning/robot/traits_pairs.csv
```

That query emits flat trait-to-mapped-trait pairs. To group those pairs back into the Phenobase
`traits.csv` shape and compare them with the canonical file, run:

```bash
python3 reasoning/refresh_traits_robot.py --skip-query \
  --input-owl reasoning/2026-05-06/ppo.owl \
  --pairs-output reasoning/robot/traits_pairs.csv
```

If you want the helper to run both the ROBOT query and the post-processing for you, use:

```bash
python3 reasoning/refresh_traits_robot.py
```

The ROBOT/SPARQL path writes:

- `reasoning/robot/traits_pairs.csv`: raw ROBOT query output
- `reasoning/robot/traits.csv`: grouped alternative traits output
- `reasoning/robot/comparison.json`: comparison to `data/traits.csv`

Current comparison summary for the local `2026-05-06` snapshot:

- `189/189` traits matched by mapped-ID set semantics
- `170/189` rows matched exactly
- the remaining differences are ordering differences in `mappedTraitIDs` and `mappedTraits`

Current reasoning artifacts:

- [reasoning/refresh_traits.py](reasoning/refresh_traits.py)
- [reasoning/refresh_traits_robot.py](reasoning/refresh_traits_robot.py)
- [reasoning/traits_build_metadata.json](reasoning/traits_build_metadata.json)
- [reasoning/2025-05-05/ppo.owl](reasoning/2025-05-05/ppo.owl)
- [reasoning/2026-05-06/ppo.owl](reasoning/2026-05-06/ppo.owl)
- [reasoning/robot/traits_pairs.sparql](reasoning/robot/traits_pairs.sparql)
- [reasoning/robot/traits_pairs.csv](reasoning/robot/traits_pairs.csv)
- [reasoning/robot/traits.csv](reasoning/robot/traits.csv)
- [reasoning/robot/comparison.json](reasoning/robot/comparison.json)
- [data/traits.csv](data/traits.csv)
- [docs/traits.csv](docs/traits.csv)
- [docs/traits-data.json](docs/traits-data.json)

Quick verification after a rebuild:

```bash
python3 -m json.tool reasoning/traits_build_metadata.json
git diff -- data/traits.csv docs/traits.csv docs/traits-data.json
python3 -m json.tool reasoning/robot/comparison.json
```

Trait mapping rules used by the rebuild:

- labels ending in ` present` are included
- labels ending in ` absent` are included
- `present` traits map to themselves plus transitive named PPO superclass traits that also end in ` present`
- `absent` traits map only to themselves

### Ingest Procedure

Use this sequence for a new Phenobase data release:

1. Refresh the ontology-driven trait mapping with `python3 reasoning/refresh_traits.py`.
2. Review `reasoning/traits_build_metadata.json` and diff `data/traits.csv`.
3. Place source `.csv` files in the release directory you want to ingest.
4. Add dataset-local `mappings.csv` and/or `transform.yaml` if source-specific cleanup, case normalization, regex mapping, null handling, or trait remapping is needed. Use `mappings.csv` for source-to-PPO ID lookup and `transform.yaml` for row-level cleanup.
5. Confirm the loader inputs are in place:

   - `data/columns.csv` defines field datatypes, requiredness, and system fields
   - `data/traits.csv` provides the PPO ID-to-`trait`-and-`mappedTraits` expansion generated by the reasoning step
   - `transform.yaml` is optional and only applies to the dataset directory being loaded

6. Run a dry ingestion pass:

```bash
python3 loader.py --mode=<machine|in_situ|herbarium> --test --no-drop-existing <data_dir>
```

7. Review `loading_errors.csv`. Common failures are:

   - missing `annotationID`
   - duplicate `annotationID` within an input file
   - empty `trait` and `trait_urn`
   - `trait_urn` values not found in `data/traits.csv`
   - legacy `trait` values not found in `data/traits.csv` when no `trait_urn` is provided
   - strict-mode coercion failures for typed fields

8. Fix the source files or the dataset-local `transform.yaml`, then rerun test mode until the release is acceptable.
9. Run the real ingestion:

```bash
python3 loader.py --mode=<machine|in_situ|herbarium> --no-drop-existing <data_dir>
```

10. If you are rebuilding the index from scratch rather than appending or updating, use:

```bash
python3 loader.py --mode=<machine|in_situ|herbarium> --drop-existing <data_dir>
```

11. If the live index needs `decadeStart`, run:

```bash
python3 update_decade_start.py --wait
```

## Loading Data

The main loader is [loader.py](loader.py).

What the loader does during a run:

- recursively finds `.csv` files under the provided dataset directory
- applies optional row-level transforms from dataset-local `transform.yaml`
- coerces values to the datatypes defined in `data/columns.csv`
- computes system fields such as `mappedTraits` and `decadeStart`
- rejects rows with missing `annotationID`, duplicate `annotationID` within a file, or unmapped traits
- writes row-level failures to `loading_errors.csv`
- indexes to `phenobase2` using `annotationID` as the Elasticsearch document `_id`

Supported loading modes:

- `machine`
- `in_situ`
- `herbarium`

Example commands:

```bash
python3 loader.py --mode=machine data/annotations.07.25.2025/ --no-drop-existing --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ data/npn.1956.01.01-2025.08.31/ --no-drop-existing --batch-size 5000 --progress-every 50000
```

### Full Reload Synopsis

For a clean rebuild, generate each dataset-specific loader CSV first, dry-run each directory, then run the real loads. The first real load should use `--drop-existing` so the Elasticsearch index starts fresh; every later dataset load should use `--no-drop-existing` so records are appended or updated into the same index.

Refresh the source CSVs:

```bash
# NPN / NEON, from downloads/npn
cd downloads/npn
python3 fetchAndTransformNPNData.py 1956-01-01 2026-05-17 ./mappings.csv
mkdir -p ingest
mv npn_observations_1956-01-01_to_2026-05-17.csv ingest/
cd ../..

# PhenoObs, from repo root
python3 downloads/phenoObs/prepare_phenoobs.py \
  --raw-root downloads/phenoObs \
  --mappings downloads/phenoObs/mappings.csv \
  --output downloads/phenoObs/ingest/phenoObs_observations.csv

# Budburst, from repo root
python3 downloads/budburst/fetch_budburst.py \
  --output downloads/budburst/ingest/budburst_observations.csv \
  --workers 3 \
  --timeout 300 \
  --retries 8

# SeasonWatch India, from repo root. Downloads the DwC-A only if missing.
python3 downloads/seasonwatchindia/fetch_seasonwatchindia.py \
  --output downloads/seasonwatchindia/ingest/seasonwatchindia_observations.csv
```

Run validation first:

```bash
python3 loader.py --mode=in_situ --test --no-drop-existing downloads/npn/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --test --no-drop-existing downloads/phenoObs/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --test --no-drop-existing downloads/budburst/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --test --no-drop-existing downloads/seasonwatchindia/ingest --batch-size 5000 --progress-every 50000
```

Run the real in-situ reload. Drop the index only on the first dataset:

```bash
python3 loader.py --mode=in_situ --drop-existing downloads/npn/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --no-drop-existing downloads/phenoObs/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --no-drop-existing downloads/budburst/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --no-drop-existing downloads/seasonwatchindia/ingest --batch-size 5000 --progress-every 50000
```

Load herbarium or machine-derived datasets after the in-situ sources. Keep using `--no-drop-existing`:

```bash
python3 loader.py --mode=herbarium --test --no-drop-existing downloads/herbarium --batch-size 5000 --progress-every 50000
python3 loader.py --mode=herbarium --no-drop-existing downloads/herbarium --batch-size 5000 --progress-every 50000
```

PhenoObs source preparation writes one loader-ready CSV, similar to the NPN transformer. The script auto-detects raw `rawdata_PhenObs_*.csv` files under `downloads/phenoObs` first, then falls back to `data/phenoObs`. The output should go into a clean loader directory that also contains `transform.yaml`:

```bash
cd downloads/phenoObs
python3 prepare_phenoobs.py ./mappings.csv --output ingest/phenoObs_observations.csv
cd ../..
python3 loader.py --mode=in_situ --test --no-drop-existing downloads/phenoObs/ingest --batch-size 5000 --progress-every 50000
python3 loader.py --mode=in_situ --no-drop-existing downloads/phenoObs/ingest --batch-size 5000 --progress-every 50000
```

From the repo root, the equivalent explicit form is:

```bash
python3 downloads/phenoObs/prepare_phenoobs.py --raw-root downloads/phenoObs --mappings downloads/phenoObs/mappings.csv --output downloads/phenoObs/ingest/phenoObs_observations.csv
```

Main options:

- `--test`: validate and simulate without writing to Elasticsearch
- `--strict`: reject rows with coercion or validation problems
- `--drop-existing`: recreate the target index before loading
- `--batch-size`: bulk size for indexing
- `--progress-every`: progress logging interval
- `--no-drop-existing`: keep the current index and upsert documents into it

What the loader relies on:

- `data/columns.csv` for schema and required fields
- `data/traits.csv` for `trait_urn` to `trait` and `mappedTraits` expansion
- optional dataset-local `transform.yaml` for normalization rules

## `transform.yaml` Structure

Place `transform.yaml` inside the dataset directory being loaded. The loader checks for
`<data_dir>/transform.yaml` automatically. If present, the file is applied row-by-row before
datatype coercion, required-field checks, and `mappedTraits` derivation.

Use `transform.yaml` for:

- trimming and case-normalizing raw source fields
- regex-based cleanup of source strings
- mapping raw trait labels to canonical Phenobase trait labels during transition
- turning source-specific null tokens into empty values
- adjusting parsing behavior for dates, booleans, and typed fields

Supported top-level keys:

- `fields`: per-field transform steps and field-level overrides
- `trait_mappings`: exact mapping from lowercase raw `trait` text to canonical trait labels. Prefer `trait_urn` in source prep, and treat this as a compatibility tool.
- `null_values`: string tokens treated as null across all fields
- `coercions`: global parsing and fallback rules for booleans, dates, integers, floats, and text

Minimal example:

```yaml
fields:
  scientificName:
    transforms:
      - op: strip
      - op: case
        rule: scientific_name_standard

  trait:
    transforms:
      - op: strip
      - op: case
        rule: lower

  year:
    datatype: integer

trait_mappings:
  breaking leaf buds: breaking leaf bud present
  flowers: flower present

null_values:
  - ""
  - na
  - n/a
  - "-"

coercions:
  text:
    case: lower
  date:
    input_formats: ["%Y-%m-%d", "%m/%d/%Y"]
    output_format: "%Y-%m-%d"
    drop_invalid: true
  boolean:
    true_values: ["true", "t", "yes", "1"]
    false_values: ["false", "f", "no", "0"]
    drop_invalid: true
  integer:
    drop_invalid: true
  float:
    drop_invalid: true
```

### Per-field Structure

Each entry under `fields` may define:

- `transforms`: ordered transform steps applied to that field
- `datatype`: override datatype for coercion
- `case`: field-level case rule applied during text coercion
- `min`: numeric minimum for integer or float fields
- `max`: numeric maximum for integer or float fields
- `input_formats`: date parse formats for that field
- `output_format`: normalized output date format for that field

Supported transform steps under `fields.<field>.transforms`:

- `op: strip`
- `op: case` with `rule: lower|upper|title|capitalize_first|scientific_name_standard`
- `op: regex_sub` with `pattern`, optional `replacement`, and optional `flags`
- `op: regex_map` with `pattern`, `to`, and optional `flags`
- `op: null_if_in` with `values`
- `op: map` with exact `values` replacements

Notes on behavior:

- transform steps run in the order written
- `trait_mappings` is only applied to the `trait` field after field transforms run; it does not replace `trait_urn`
- `trait_mappings` keys should be lowercase because the loader lowercases the incoming trait before lookup
- `regex_map` uses regex `match`, so patterns should usually be anchored if you want full-value matching
- supported regex flags are `IGNORECASE`, `MULTILINE`, and `DOTALL`
- `null_values` is global, while `null_if_in` applies only to one field
- `text.case` under `coercions` is a global default for text fields, but field-level `case` overrides it
- invalid integers, floats, booleans, and dates are nulled by default unless `--strict` is used, in which case those coercion problems reject the row

Example with more complete field transforms:

```yaml
fields:
  recordedBy:
    transforms:
      - op: strip
      - op: regex_sub
        pattern: "\\s+"
        replacement: " "

  basisOfRecord:
    transforms:
      - op: strip
      - op: map
        values:
          specimen: PreservedSpecimen
          photo: HumanObservation

  date:
    input_formats: ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]
    output_format: "%Y-%m-%d"

  latitude:
    datatype: float
    min: -90
    max: 90

  longitude:
    datatype: float
    min: -180
    max: 180

  trait:
    transforms:
      - op: strip
      - op: regex_map
        pattern: "^open flowers?$"
        to: flower present
        flags: IGNORECASE

trait_mappings:
  flowering: flower present
  no flowers: flower absent
```

## Export And Maintenance Utilities

### Download A CSV Dump

Use [download_csv_dump.py](download_csv_dump.py) to scroll through the public Phenobase query API and write a local CSV.

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

Use [update_decade_start.py](update_decade_start.py) to add the mapping and backfill `decadeStart` on an existing live index without reloading source files.

```bash
python3 update_decade_start.py
python3 update_decade_start.py --wait
python3 update_decade_start.py --requests-per-second 200
```

## Pages And Shared Outputs

The `docs/` folder is intended for GitHub Pages publication and for quick sharing with collaborators.

Published outputs:

- [docs/index.html](docs/index.html): GitHub Pages entry point redirecting to the traits viewer
- [docs/traits.html](docs/traits.html): rendered trait explorer
- [docs/traits.csv](docs/traits.csv): published CSV copy
- [docs/traits-data.json](docs/traits-data.json): viewer payload

## Core Files

- [data/traits.csv](data/traits.csv): ontology-derived trait mapping
- [data/columns.csv](data/columns.csv): field definitions and schema metadata
- [trait_lookup.py](trait_lookup.py): local helper for resolving PPO IDs to current labels
- [loader.py](loader.py): ingestion driver
- [reasoning/refresh_traits.py](reasoning/refresh_traits.py): reasoning rebuild driver
- [download_csv_dump.py](download_csv_dump.py): API export helper
- [update_decade_start.py](update_decade_start.py): live index backfill helper

## Requirements

- Python 3.8+
- Elasticsearch reachable for ingestion or maintenance commands

## License

This repository is licensed under the [MIT License](LICENSE). Bundled ontology snapshots and other third-party source data may remain subject to their own upstream terms.

## Author

PhenoBase Project | Biocode, LLC
