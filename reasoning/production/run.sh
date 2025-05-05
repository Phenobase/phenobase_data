#!/bin/bash
#curl -L https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/2025-04-release/ppo.owl -o ppo.owl 
#robot reason --reasoner HermiT -i ppo.owl -o ppo_reasoned.owl 
robot query -i ppo_reasoned.owl --query query.sparql results.tsv 
echo 'trait_id,trait_name,mapped_traits' > results.csv
awk 'NR>1 { gsub("\t", ","); print }' results.tsv > results.csv
