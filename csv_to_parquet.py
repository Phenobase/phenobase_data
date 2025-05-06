import csv
import datetime
import os
import argparse
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

def str_to_bool(s):
    return s.lower() == "true"

def is_valid_lat_lon(value, min_value, max_value):
    try:
        num = float(value)
        return min_value <= num <= max_value
    except ValueError:
        return False

def is_integer(value):
    if value.lower() == 'na':
        return True, ''
    try:
        return True, int(value)
    except ValueError:
        return False, value

def load_traits_mapping(csv_file_path):
    traits_mapping = {}
    with open(csv_file_path, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            traits_mapping[row['trait']] = row['mapped_traits']
    return traits_mapping

def get_mapped_traits(traits_mapping, search_trait):
    return traits_mapping.get(search_trait)

def get_files(dir, ext='csv'):
    for root, dirs, files in os.walk(dir):
        for file in files:
            if file.endswith(ext):
                yield os.path.join(root, file)

def log_error(guid, errors):
    with open('parquet_errors.csv', 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([guid, '|'.join(errors)])

def process_csv(file, traits_mapping):
    valid_rows = []
    with open(file, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            errors = []
            guid = row.get('machine_learning_annotation_id', 'UNKNOWN')

            if row['prediction_class'].lower() != 'detected':
                errors.append("prediction class must be 'detected'")
            if row['certainty'].lower() != 'high':
                errors.append("certainty must be 'high'")

            for field in ['coordinate_uncertainty_meters', 'year', 'day_of_year']:
                is_valid, row[field] = is_integer(row.get(field, ''))
                if not is_valid:
                    errors.append(f"{field} must be integer or blank")

            trait = row['trait'] + (' absent' if row['certainty'] == 'low' else ' present')
            mapped = get_mapped_traits(traits_mapping, trait)
            if mapped:
                row['mapped_traits'] = mapped
            else:
                errors.append("Could not map trait")

            if not is_valid_lat_lon(row.get('latitude', ''), -90, 90) or \
               not is_valid_lat_lon(row.get('longitude', ''), -180, 180):
                row['location'] = ''
                row['latitude'] = ''
                row['longitude'] = ''
                errors.append("Invalid latitude/longitude")
            else:
                row['location'] = f"{row['latitude']},{row['longitude']}"

            if errors:
                log_error(guid, errors)
            else:
                valid_rows.append(row)

    return valid_rows

def main(data_dir, traits_path, output_path):
    traits_mapping = load_traits_mapping(traits_path)
    all_valid_data = []

    for file in get_files(data_dir):
        print(f"Processing {file}")
        all_valid_data.extend(process_csv(file, traits_mapping))

    if all_valid_data:
        df = pd.DataFrame(all_valid_data)
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_path)
        print(f"✅ Saved {len(df)} records to {output_path}")
    else:
        print("❌ No valid records found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Convert validated CSVs to Parquet')
    parser.add_argument('data_dir', help="Directory of CSV files")
    parser.add_argument('--traits', default='data/traits.csv', help="Path to traits mapping file")
    parser.add_argument('--output', default='output_data.parquet', help="Output Parquet file path")
    args = parser.parse_args()

    main(args.data_dir, args.traits, args.output)

