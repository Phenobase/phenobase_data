curl -o ppo.owl https://raw.githubusercontent.com/PlantPhenoOntology/ppo/odk-conversion/ppo.owl

robot query --input ppo.owl --query query.sparql results.tsv

cat results.tsv
