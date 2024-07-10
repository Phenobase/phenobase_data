# Loading phenobase data

```
python loader.py sample true
```

# Field Definitions

Here is a list of field values with their data types and definitions.

## Table

| Field                        | Datatype | Source | Definition                                                  |
|------------------------------|----------|--------|-------------------------------------------------------------|
| guid                         | text     | user   | global unique identifier for the observation                |
| datasource                   | keyword  | user   | name of the datasource                                      |
| verbatim_date                | text     | user   | verbatim date                                               |
| day_of_year                  | integer  | user   | day of the year. Must be between 1 and 366                  |
| year                         | integer  | user   | year of the observation                                     |
| latitude                     | float    | user   | latitude with precision up to 6 decimal places              |
| longitude                    | float    | user   | longitude with precision up to 6 decimal places             |
| coordinate_uncertainty_meters| integer  | user   | coordinate uncertainty in meters                            |
| family                       | keyword  | user   | family of the organism                                      |
| scientific_name              | text     | user   | scientific name of the observed organism                    |
| taxon_rank                   | text     | user   | taxonomic rank of the scientific name                       |
| basis_of_record              | text     | user   | basis of record of observed organism. See Darwin Core reference for values |
| individualID                 | text     | user   | individual ID of the observed organism                      |
| occurrenceID                 | text     | user   | occurrence ID of the observed organism                      |
| verbatim_trait               | text     | user   | Verbatim description of the trait                           |
| trait                        | text     | user   | Trait tested. Currently "flower" or "fruit"                 |
| observed_image_guid          | text     | user   | Globally Unique Identifier of the image                     |
| observed_image_url           | text     | user   | URL for the image                                           |
| certainty                    | keyword  | user   | Certainty of machine observation (Equivocal or Unequivocal) |
| model_uri                    | text     | user   | URI of the model                                            |
| error_message                | text     | user   | Error messages from processing                              |
| mapped_traits                | keyword  | system | Pipe delimited list of mapped traits                        |

## YAML

```yaml
fields:
  - field: guid
    datatype: text
    source: user
    definition: global unique identifier for the observation
  - field: datasource
    datatype: keyword
    source: user
    definition: name of the datasource
  - field: verbatim_date
    datatype: text
    source: user
    definition: verbatim date
  - field: day_of_year
    datatype: integer
    source: user
    definition: day of the year. Must be between 1 and 366
  - field: year
    datatype: integer
    source: user
    definition: year of the observation
  - field: latitude
    datatype: float
    source: user
    definition: latitude with precision up to 6 decimal places
  - field: longitude
    datatype: float
    source: user
    definition: longitude with precision up to 6 decimal places
  - field: coordinate_uncertainty_meters
    datatype: integer
    source: user
    definition: coordinate uncertainty in meters
  - field: family
    datatype: keyword
    source: user
    definition: family of the organism
  - field: scientific_name
    datatype: text
    source: user
    definition: scientific name of the observed organism
  - field: taxon_rank
    datatype: text
    source: user
    definition: taxonomic rank of the scientific name
  - field: basis_of_record
    datatype: text
    source: user
    definition: basis of record of observed organism. See Darwin Core reference for values
  - field: individualID
    datatype: text
    source: user
    definition: individual ID of the observed organism



