# Elasticsearch Loader Script

## Overview

This script loads tabular data into Elasticsearch from CSV/TSV files. It performs validation using rules defined in `columns.csv` and data presence in `traits.csv`.

The script supports three loading modes:
- `machine`: for loading machine observation data
- `inat`: for iNaturalist observation data
- `herbarium`: for herbarium record data

Each mode uses specific required fields defined in `columns.csv`.

---

## Usage

```bash
python loader.py [--data_dir DIR] [--drop_index] --mode {machine,inat,herbarium}
```

### Options

- `--data_dir DIR`: Optional. Directory containing data files to be loaded (e.g., TSV/CSV files). Defaults to current directory.
- `--drop_index`: Optional. If specified, drops the existing Elasticsearch index before loading.
- `--mode {machine,inat,herbarium}`: Required. Specifies the type of data being loaded. Determines required fields and validation rules.

### Example

```bash
python loader.py --data_dir ./data --drop_index --mode inat
```

---

## Under the Hood

### `traits.csv`

- This file contains individual records to load into Elasticsearch.
- Each row is validated based on the required fields specified for the selected mode.
- Invalid rows are reported (with reason) but skipped from indexing.

### `columns.csv`

- Defines schema for all fields that can be used in the Elasticsearch index.
- Contains columns:
  - `field`: The name of the field in the data.
  - `datatype`: The expected type (e.g., `text`, `integer`, `float`, `boolean`, `date`, `keyword`, etc.)
  - `machine_required`, `inat_required`, `herbarium_required`: Indicates if the field is required for a given mode.
- Used for two purposes:
  1. Validating presence of required fields.
  2. Building Elasticsearch mappings dynamically.

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

PhenoBase Project | Deck Family Farm
