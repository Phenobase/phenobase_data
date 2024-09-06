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
