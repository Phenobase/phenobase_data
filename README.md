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
python loader.py [--data_dir DIR] [--drop_index] --mode {machine,in_situ,herbarium}
```

### Options

- `--data_dir DIR`: Optional. Directory containing data files to be loaded (e.g., TSV/CSV files). Defaults to current directory.
- `--drop_index`: Optional. If specified, drops the existing Elasticsearch index before loading.
- `--mode {machine,inat,herbarium}`: Required. Specifies the type of data being loaded. Determines required fields and validation rules.

### Example

```bash
# here is an example load script
python loader.py --mode=machine data/annotations.07.25.2025/ false --batch-size 5000 --progress-every 50000
python loader.py --mode=in_situ data/npn.1956.01.01-2025.08.31/ false --batch-size 5000 --progress-every 50000
```

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
