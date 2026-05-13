# Elasticsearch Loader Script

## Overview

The `loader.py` script loads tabular data into Elasticsearch from CSV/TSV files. It performs validation using rules defined in `data/columns.csv` and data presence in `data/traits.csv`.

The script supports three loading modes:
- `machine`: for loading machine observation data
- `in_situ`: for in_situ observations
- `herbarium`: for herbarium record data

Each mode uses specific required fields defined in `columns.csv`.

---

## Usage

```bash
usage: loader.py [-h] --mode {machine,in_situ,herbarium} [--test] [--strict] [--batch-size BATCH_SIZE] [--progress-every PROGRESS_EVERY] data_dir drop_existing
loader.py: error: the following arguments are required: data_dir, drop_existing, --mode
```

### Options

```
Positional

data_dir Directory containing CSV files to load.

Options

--mode {machine,in_situ,herbarium} (required)
--drop-existing / --no-drop-existing (default: --no-drop-existing)
--test Test mode (no ES insert).
--strict Reject rows with invalid field values after coercion.
--batch-size N Docs per bulk request (default: 5000).
--progress-every N Print progress every N rows (default: 50000).
```

### Example

```bash
# here is an example load script
python loader.py --mode=machine data/annotations.07.25.2025/ --no-drop-existing --batch-size 5000 --progress-every 50000
python loader.py --mode=in_situ data/npn.1956.01.01-2025.08.31/ --no-drop-existing --batch-size 5000 --progress-every 50000
```

### Backfill `decadeStart` on the live index

Use the helper script in the repo root to add the mapping and run `_update_by_query` against the existing index without reloading source files:

```bash
python update_decade_start.py
```

Wait for completion synchronously:

```bash
python update_decade_start.py --wait
```

Throttle the job if needed:

```bash
python update_decade_start.py --requests-per-second 200
```

### Download a CSV dump through the API

Use the helper script in the repo root to call the public Phenobase query API and scroll through the index until all matching rows are written to a local CSV.

The script follows the same double-slash proxy convention already used by `phenobase_interface`, so it works with the current `biscicol-server` path-rewrite behavior.

```bash
python download_csv_dump.py
```

By default this:

- queries the `phenobase2` index
- uses a Lucene query of `*`
- requests 5,000 rows per scroll page
- keeps scrolling until the API returns no more hits
- writes the CSV to `downloads/phenobase_dump.csv`

Export a filtered subset with a Lucene query:

```bash
python download_csv_dump.py --query 'genus:Quercus AND year:[2000 TO 2025]' --output downloads/quercus.csv
```

Cap the export locally if needed:

```bash
python download_csv_dump.py --limit 100000
```

Tune the scroll page size or point at a different API base URL:

```bash
python download_csv_dump.py --batch-size 10000 --scroll 1m
python download_csv_dump.py --base-url https://biscicol.org/phenobase/api/v1/query --index phenobase2
```

This script uses `data/columns.csv` to define CSV column order, which keeps the output aligned with the schema used by the loader.

---

## Under the Hood

### `traits.csv`

- This file contains trait mappings from ontology trait terms to a pipe delimited list of parent terms

### `columns.csv`

- Defines schema for all fields that can be used in the Elasticsearch index.
- Contains columns:
  - `field`: The name of the field in the data.
  - `datatype`: The expected type (e.g., `text`, `integer`, `float`, `boolean`, `date`, `keyword`, etc.)
  - `machine_required`, `inat_required`, `herbarium_required`: Indicates if the field is required for a given mode.
- Used for two purposes:
  1. Validating presence of required fields.
  2. Building Elasticsearch mappings dynamically.

### `transform.yaml` (Optional)

A per-dataset YAML file for applying simple value transformations before ingestion.
If transform.yaml is present in the data_dir, it is loaded automatically.
Only the trait field is currently transformed using this mechanism.

Format:
```
trait_mappings:
  green leaves present: non-senescing unfolded true leaves present
  senescent leaves: senescing leaves present
  red leaves: colored leaves (non-green)
```
If a value in the trait column matches a key in trait_mappings (case-insensitive), it is replaced by the corresponding value before validation or Elasticsearch indexing.

This allows for normalizing heterogeneous trait values across datasets without modifying the main loader script.

### Elasticsearch Mapping

- The script uses `columns.csv` to generate the index mapping.
- If `--drop_index` is passed, the script deletes the existing index and re-creates it using the generated mapping.

### Error Reporting

- Rows missing required fields or containing invalid values are logged.
- A summary count of invalid rows is displayed after loading.

---

## Requirements

- Python 3.8+
- Elasticsearch running locally or remotely (endpoint configured in script or via `.env` file)
- `pandas`, `elasticsearch`, `python-dotenv`

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Notes

- The index name is determined by mode (e.g., `inat-records`, `machine-records`, etc.)
- Validation logic may be extended by modifying the script.
- Ensure that `columns.csv` and `traits.csv` are present in the working directory or specified via `--data_dir`.

---

## Author

PhenoBase Project | Biocode, LLC
