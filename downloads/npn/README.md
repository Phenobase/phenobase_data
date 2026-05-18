# NPN Observations: Fetch + Transform

This tool downloads USA-NPN observations for a given date range and maps phenophase descriptions to standardized PPO traits using a `mappings.csv`. Rows are dropped if:
- the mapping marks that description as `status = -1` (drop),
- there’s no mapping for the description/status,
- or the resolved mapping cannot be tied to a PPO ID / `trait_urn`.

> **Script:** `fetchAndTransformNPNData.py`

---

## Prerequisites

- **Python** 3.8+
- An internet connection (calls the USA-NPN API)
- A CSV file named `mappings.csv` with headers:

```csv
verbatim_trait,status,trait_urn,trait
```

---

## Files

- `fetchAndTransformNPNData.py` - main script
- `mappings.csv` - mapping table (default path `/mnt/data/mappings.csv`, or pass a custom path)
- Output: `npn_observations_<start>_to_<end>.csv` in the current directory

---

## Usage

```bash
python3 fetchAndTransformNPNData.py <start_date> <end_date> [mappings_csv_path]
```

- `<start_date>` / `<end_date>`: `YYYY-MM-DD`
- `[mappings_csv_path]` (optional): path to `mappings.csv`. If omitted, defaults to `/mnt/data/mappings.csv`.

### Examples

Use default mappings path:

```bash
python3 fetchAndTransformNPNData.py 2025-08-01 2025-08-02
```

Use a local mappings file:

```bash
python3 fetchAndTransformNPNData.py 2025-08-01 2025-08-31 ./mappings.csv
```

---

## `mappings.csv` Format

- **`verbatim_trait`**: The exact phenophase description as returned by the API (matching is case- and whitespace-insensitive; non-breaking spaces are normalized).
- **`status`**: One of `-1`, `0`, `1`.
  - `-1` means drop all rows with this description.
  - `0` or `1` provides the mapping used when the observation’s `phenophase_status` is absent (`0`) or present (`1`).
- **`trait_urn`**: The PPO ID for the standardized trait. This is the stable key. The script resolves the current canonical `trait` label from `data/traits.csv`.
- **`trait`**: The human-readable label for the trait. This is still written to output for compatibility, but the ID is the source of truth.

Example:

```csv
verbatim_trait,status,trait_urn,trait
Open flowers,1,PPO:0002333,open flower present
Open flowers,0,PPO:0002632,open flower absent
Leaf out,-1,,
```

---

## What the Script Does

1. Splits the requested date range into month-sized chunks.
2. Fetches observations from the USA-NPN JSON API.
3. Normalizes each `phenophase_description` and looks it up in `mappings.csv`.
4. Applies mapping rules:
   - Drop if `-1` mapping exists for that description.
   - Otherwise prefer an exact mapping for the observation’s `phenophase_status` (`0`/`1`) with a non-empty trait.
   - If exact is missing, fallback to a non-empty `1` mapping, then `0`.
   - If all else fails or trait is empty, drop.
5. Writes a CSV with columns:

```text
dataSource,scientificName,taxonRank,basisOfRecord,family,genus,species,
annotationID,date,year,dataset_id,site_id,individual_id,dayOfYear,
latitude,longitude,verbatimTrait,phenophase_status,trait_urn,trait
```

During the run, you’ll see summary counters:

```text
Kept rows: <n> | Dropped (-1 map): <n> | Dropped (empty trait): <n> | Dropped (no map): <n> | Dropped (bad obs status): <n>
```

---

## Output

The script creates a file like:

```text
npn_observations_2025-08-01_to_2025-08-02.csv
```

Only rows with a valid, mapped `trait_urn` and `trait` are included.

---

## Copy the Output to `../../data/npn.09.01.2025`

After a successful run, copy the generated CSV into your dated folder:

```bash
mkdir -p ../../data/npn.08.01.2025-08.02.2025
cp npn_observations_2025-08-01_to_2025-08-02.csv ../../data/npn.08.01.2025-08.02.2025/
```

If you run multiple ranges, copy each resulting `npn_observations_*.csv` you want to keep into the same folder.

---

## Troubleshooting

- **Some descriptions aren’t mapping**
  Ensure `verbatim_trait` in `mappings.csv` matches the API’s `phenophase_description` text. Add rows for missing descriptions.

- **`-1` rows not dropping**
  Check that the `status` cell is truly `-1` and not blank. The script treats any `-1` row for a description as a hard drop.

---

## License

Internal utility script. Use within your organization/project as needed.
