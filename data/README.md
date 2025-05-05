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
To generate all_traits.csv, give chat the following:
```I’m working with the Plant Phenology Ontology at  
https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl  
 
• Find every phenology class that ends in “present” or “absent”.  
• Keep ONLY those presence/absence classes (no “bud”, “structure”, etc. unless the label itself ends in present/absent).  
• For each of those classes, list the full ancestor chain **upward until you reach**  
  – “plant structure present” or “plant structure absent”  
  (stop there—do not include higher nodes like *individual plant phenology*).  
• Return the result as a CSV with four columns:  
    1. `trait_id` (the PPO IRI)  
    2. `trait_name` (the label)  
    3. `category` (always `phenology`)  
    4. `mapped_traits` (the ancestor chain you found, in order, separated with a pipe `|`)  
• Output ONLY the CSV block in the reply—no extra narrative.
```
