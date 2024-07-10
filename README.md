# Structuring Data

The following tables define the expected structure of incoming data:

- The loader script expects columns named according to the values in the [data/columns.csv](data/columns.csv) using CSV, comma separated value format.
- [basis_of_record](data/basis_of_record.csv) controlled vocabulary
- [certainty](data/certainty.csv) controlled vocabulary
- [trait)](data/traits.csv) controlled vocabulary.  See "trait" column.
- [datasource](data/datasource.csv) controlled vocabulary for all datasources we are working with.   Put new datasource in directory named according to the datasource itself.  For example, "sample" goes in a directory called "data/sample".  Only load data files less than 10,000 records in github.  All others will be added to .gitignore file


# Sample Query Page
We can visualize data that is loaded at our beta [Phenobase Query Page](https://phenobase.netlify.app/)

# Script for loading data

```
python loader.py sample true
```

