# Reasoning

This directory contains the reproducible workflow for rebuilding [data/traits.csv](../data/traits.csv) from the current PPO ontology.

## Source Of Truth

For this workflow we intentionally use the GitHub `main` ontology file, not the public PURL:

`https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl`

Reason: the GitHub file is currently newer than the public PURL and is the source the project wants to use for this refresh.

## What Gets Rebuilt

The refresh script writes:

- `data/traits.csv`: the loader lookup used to expand a trait into `mappedTraits`
- `reasoning/<version>/ppo.owl`: a local snapshot of the ontology used for that build
- `reasoning/traits_build_metadata.json`: build metadata including source URL and ontology version
- `docs/traits.csv`: published copy of the generated traits file for GitHub Pages
- `docs/traits-data.json`: static JSON payload used by the GitHub Pages trait viewer

## Rebuild Command

From the repo root:

```bash
python3 reasoning/refresh_traits.py
```

Compatibility wrapper:

```bash
./reasoning/get_traits.sh
```

## How The Mapping Is Built

The script parses PPO classes directly from the ontology and selects classes whose labels end with:

- ` present`
- ` absent`

For each selected PPO class:

- `present` traits map to themselves plus the transitive closure of named PPO superclass traits whose labels also end with ` present`
- `absent` traits map only to themselves

This matches the semantics currently used by `loader.py`, where `trait` is resolved to a pipe-delimited `mappedTraits` list.

## Reproducing A Build

1. Run `python3 reasoning/refresh_traits.py`
2. Check the ontology version printed at the end
3. Inspect `reasoning/traits_build_metadata.json`
4. Diff `data/traits.csv` against the previous version

Useful checks:

```bash
python3 -m json.tool reasoning/traits_build_metadata.json
git diff -- data/traits.csv
```

Published pages:

- Workflow landing page: `https://phenobase.github.io/phenobase_data/`
- Rendered traits explorer: `https://phenobase.github.io/phenobase_data/traits.html`

## Notes

- the script uses only the Python standard library, so no local Python package install is required
