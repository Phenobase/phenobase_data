Fetch NPN Data

First, need to ensure that phenophase_descriptions.csv is populated with mappings to 
latest PPO ontology

Here is the script for fetching phenophase data

Beginning with: 
Create a new file with first year of record of observations:
```
node fetchPhenobaseData.js 1956-01-01 1956-12-31 FALSE
```

Appending Data:
```
node fetchPhenobaseData.js 1957-01-01 2010-12-31 TRUE
node fetchPhenobaseData.js 2011-01-01 2011-12-31 TRUE
node fetchPhenobaseData.js 2012-01-01 2012-12-31 TRUE
node fetchPhenobaseData.js 2013-01-01 2013-12-31 TRUE
node fetchPhenobaseData.js 2014-01-01 2014-12-31 TRUE
node fetchPhenobaseData.js 2015-01-01 2015-12-31 TRUE
node fetchPhenobaseData.js 2016-01-01 2016-12-31 TRUE
node fetchPhenobaseData.js 2017-01-01 2017-12-31 TRUE
node fetchPhenobaseData.js 2018-01-01 2018-12-31 TRUE
node fetchPhenobaseData.js 2019-01-01 2019-12-31 TRUE
node fetchPhenobaseData.js 2020-01-01 2020-12-31 TRUE
node fetchPhenobaseData.js 2021-01-01 2021-12-31 TRUE
node fetchPhenobaseData.js 2022-01-01 2022-12-31 TRUE
node fetchPhenobaseData.js 2023-01-01 2023-12-31 TRUE
node fetchPhenobaseData.js 2024-01-01 2024-11-14 TRUE
```


