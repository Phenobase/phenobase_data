[![Netlify Status](https://api.netlify.com/api/v1/badges/9aba2cd4-5eaa-4879-8e51-2c11c26a1719/deploy-status)](https://app.netlify.com/sites/phenobase/deploys)


# Structuring Data

The following tables define the expected structure of incoming data:

- [all columns](data/columns.csv)
- [basis_of_record value](data/basis_of_record.csv)
- [certainty values](data/certainty.csv)
- [trait values (see trait column)](data/traits.csv)
- [datasource values](data/datasource.csv)


# Sample Query Page
We can visualize data that is loaded at our beta [Phenobase Query Page](https://phenobase.netlify.app/)

# Script for loading data

```
python loader.py sample true
```

