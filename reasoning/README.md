# Reasoning

Here are scripts and tools in this directory to support populating the [traits](../data/traits.csv) file


## Check for most recent ppo.owl
```
curl -L https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/2025-04-release/ppo.owl -o ppo.owl 
```

## Create reasoned file
```
robot reason --reasoner HermiT -i ppo.owl -o ppo_reasoned.owl 
```

## Run the query and post‑process
robot query -i ppo_reasoned.owl --query query.sparql traits.tsv 


# Create a CSV file
```
echo 'trait_id,trait_name,category,mapped_traits' > traits.csv
awk 'NR>1 { gsub("\t",","); print $0 ",phenology" }'traits.tsv >> traits.csv
```


