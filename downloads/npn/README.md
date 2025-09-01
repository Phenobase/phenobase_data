# NPN Observations: Fetch + Transform

This tool downloads USA-NPN observations for a given date range and **maps phenophase descriptions to standardized traits** using a `mappings.csv`. Rows are **dropped** if:
- the mapping marks that description as `status = -1` (drop),
- there’s **no** mapping for the description/status,
- or the resolved mapping’s `trait` is **empty**.

> **Script:** `fetchAndTransformNPNData.js`

---

## Prerequisites

- **Node.js** v18+ (recommended)
- An internet connection (calls the USA-NPN API)
- A CSV file named `mappings.csv` with headers:
  ```
  verbatim_trait,status,trait
  ```

### Install dependencies

```bash
npm i axios fast-csv date-fns
```

---

## Files

- `fetchAndTransformNPNData.js` — main script
- `mappings.csv` — mapping table (default path `/mnt/data/mappings.csv`, or pass a custom path)
- Output: `npn_observations_<start>_to_<end>.csv` in the current directory

---

## Usage

```bash
node fetchAndTransformNPNData.js <start_date> <end_date> [mappings_csv_path]
```

- `<start_date>` / `<end_date>`: `YYYY-MM-DD`
- `[mappings_csv_path]` (optional): path to `mappings.csv`. If omitted, defaults to `/mnt/data/mappings.csv`.

### Examples

Use default mappings path:

```bash
node fetchAndTransformNPNData.js 2025-08-01 2025-08-02
```

Use a local mappings file:

```bash
node fetchAndTransformNPNData.js 2025-08-01 2025-08-31 ./mappings.csv
```

---

## `mappings.csv` Format

- **`verbatim_trait`**: The exact phenophase description as returned by the API (matching is case- and whitespace-insensitive; non-breaking spaces are normalized).
- **`status`**: One of `-1`, `0`, `1`.
  - `-1` means **drop all rows** with this description (wins over any other mapping).
  - `0` or `1` provides a **trait** to use when the observation’s `phenophase_status` is absent (`0`) or present (`1`).
- **`trait`**: The standardized trait string to write into the output. If this is **empty**, the row is dropped.

Example:

```csv
verbatim_trait,status,trait
Open flowers,1,Phenophase: Open flowers (present)
Open flowers,0,Phenophase: Open flowers (absent)
Leaf out,-1,
```

> The loader is robust to Unicode minus signs (e.g., `−1`) and common text synonyms such as `present/absent/observed/yes/no/ignore/omit`.

---

## What the Script Does

1. Splits the requested date range into month-sized chunks.
2. Fetches observations from the USA-NPN JSON API.
3. Normalizes each `phenophase_description` and looks it up in `mappings.csv`.
4. Applies mapping rules:
   - Drop if `-1` mapping exists for that description.
   - Otherwise prefer an **exact** mapping for the observation’s `phenophase_status` (0/1) with a **non-empty** `trait`.
   - If exact is missing, fallback to a non-empty `1` mapping, then `0`.
   - If all else fails or trait is empty → **drop**.
5. Writes a CSV with columns:
   ```
   genus,species,observation_id,observation_date,year,dataset_id,day_of_year,
   latitude,longitude,phenophase_description,phenophase_status,trait
   ```
   where `phenophase_status` is rendered as `Observed` / `Not Observed`.

During the run, you’ll see summary counters:
```
Kept rows: <n> | Dropped (-1 map): <n> | Dropped (empty trait): <n> | Dropped (no map): <n> | Dropped (bad obs status): <n>
```

---

## Output

The script creates a file like:

```
npn_observations_2025-08-01_to_2025-08-02.csv
```

Only rows with a valid, mapped **trait** are included.

---

## Copy the Output to `../../data/npn.09.01.2025`

After a successful run, copy the generated CSV into your dated folder:

```bash
# Make sure the destination directory exists
mkdir -p ../../data/npn.09.01.2025

# Replace the filename below with the actual output name from your run
cp npn_observations_2025-08-01_to_2025-08-02.csv ../../data/npn.09.01.2025/
```

If you run multiple ranges, copy each resulting `npn_observations_*.csv` you want to keep into the same folder.

---

## Troubleshooting

- **Some descriptions aren’t mapping**
  Ensure `verbatim_trait` in `mappings.csv` matches the API’s `phenophase_description` text (case and spacing don’t matter; trailing spaces / NBSPs are normalized). Add rows for missing descriptions.

- **`-1` rows not dropping**
  Check that the `status` cell is truly `-1` and not blank or a typographic minus (the script normalizes both, but blanks are ignored). The loader also sets an internal drop flag for any description with `-1`, so drops should always win.

- **`package.json` files ignored by git**
  If you added `*.json` to `.gitignore`, use exceptions for `package.json` and `package-lock.json`:
  ```gitignore
  *.json
  !package.json
  !package-lock.json
  ```

---

## License

Internal utility script. Use within your organization/project as needed.
