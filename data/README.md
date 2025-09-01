The data folder is where we manage data to load into phenobase.
Actual data files we don't want to commit to github since whey are too large.

1. Download data from google drive using the `download_gdrive.py` script 
```
$python download_gdrive.py
Usage: python download_gdrive.py <Google Drive file link> <destination file path>
```

2. Split the data files using the `split_csv.py` script.  We want to chunk data into groups of 100,000
This script lets you specify the amount on the command line.  Note, be sure to remove the input_filename
from the specified directory so we do not load records twice
```
$ python split_csv.py
Usage: python split_csv.py <input_filename> <lines_per_file>
```

### Robot one‑liner to create `traits.csv`

```bash
# Download PPO, reason it, query for every class whose label ends in
# “present” or “absent”, build a pipe‑delimited ancestor chain up to
# plant structure present/absent, and write a PhenoBase‑compatible CSV
curl -L https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl -o ppo.owl && \
robot reason --reasoner HermiT -i ppo.owl -o ppo_reasoned.owl && \
robot query -i ppo_reasoned.owl \
  --query <(cat <<'SPARQL'
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?trait_id ?trait_name (GROUP_CONCAT(?anc_label; SEPARATOR="|") AS ?mapped_traits)
WHERE {
  ?trait_id rdfs:label ?trait_name .
  FILTER (STR(?trait_name) =~ "(?i)(present|absent)$")
  VALUES ?stop {
    <http://purl.obolibrary.org/obo/ppo_0001200>  # plant structure present
    <http://purl.obolibrary.org/obo/ppo_0001201>  # plant structure absent
  }
  ?trait_id (rdfs:subClassOf)+ ?anc .
  ?anc rdfs:label ?anc_label .
  FILTER EXISTS { ?anc (rdfs:subClassOf)* ?stop }
}
GROUP BY ?trait_id ?trait_name
ORDER BY ?trait_name
SPARQL
) \
  ppo_traits.tsv && \
{ echo "trait_id,trait_name,category,mapped_traits" ; \
  awk 'NR>1{gsub("\t",","); print $0 ",phenology"}' ppo_traits.tsv ; } > ppo_traits.csv
```
