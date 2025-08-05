# -*- coding: utf-8 -*-
import os
import csv
import sys
import warnings
import argparse
from elasticsearch import Elasticsearch, helpers
import uuid
import datetime
import yaml

# Suppress all warnings including LibreSSL ones
warnings.filterwarnings("ignore")

def load_column_metadata(path='data/columns.csv'):
    metadata = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            metadata[row['field']] = row
    return metadata

def build_es_mapping(column_metadata):
    es_type_map = {
        'text': {'type': 'text'},
        'keyword': {'type': 'keyword'},
        'integer': {'type': 'integer'},
        'float': {'type': 'float'},
        'geo_point': {'type': 'geo_point'}
    }

    return {
        "mappings": {
            "properties": {
                field: es_type_map.get(meta['datatype'].strip().lower(), {"type": "text"})
                for field, meta in column_metadata.items()
            }
        }
    }

def load_traits_mapping(path='data/traits.csv'):
    traits_mapping = {}
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trait = row['trait'].strip().lower()
            traits_mapping[trait] = row['mappedTraits']
    return traits_mapping

traits_mapping = load_traits_mapping()



def load_yaml_mapping(path):
    if not os.path.exists(path):
        print(f"⚠️ No transform.yaml found at {path}")
        return {}

    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def make_row_transformer(transform_path):
    yaml_rules = load_yaml_mapping(transform_path)

    def transform_row(row):
        trait_val = row.get('trait', '').strip().lower()
        if trait_val and 'trait_mappings' in yaml_rules:
            mapped = yaml_rules['trait_mappings'].get(trait_val)
            if mapped:
                row['trait'] = mapped
        return row

    return transform_row

from datetime import datetime

def _lower_str(x):
    return x.lower().strip() if isinstance(x, str) else x

def _is_null_token(v, yaml_rules):
    nulls = set([_lower_str(n) for n in (yaml_rules.get('null_values') or [])])
    return isinstance(v, str) and _lower_str(v) in nulls

def _parse_bool(v, yaml_rules):
    rules = yaml_rules.get('coercions', {}).get('boolean', {})
    tvals = set([_lower_str(x) for x in rules.get('true_values', [])])
    fvals = set([_lower_str(x) for x in rules.get('false_values', [])])
    lv = _lower_str(v)
    if lv in tvals: return True
    if lv in fvals: return False
    raise ValueError(f"Invalid boolean: {v}")

def _parse_date(v, field_rules, global_rules):
    # field-specific formats override global formats
    fmts = field_rules.get('input_formats') or global_rules.get('coercions', {}).get('date', {}).get('input_formats') or ["%Y-%m-%d"]
    out = field_rules.get('output_format') or global_rules.get('coercions', {}).get('date', {}).get('output_format') or "%Y-%m-%d"
    last_err = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(v, fmt)
            return dt.strftime(out)
        except Exception as e:
            last_err = e
    raise ValueError(f"Invalid date '{v}'; tried formats {fmts}. Last error: {last_err}")

def _effective_datatype(field, column_metadata, yaml_rules):
    # field-specific override wins; else columns.csv datatype
    fld = yaml_rules.get('fields', {}).get(field, {})
    dt = (fld.get('datatype') or column_metadata.get(field, {}).get('datatype') or 'text').strip().lower()
    return dt, fld  # return also the field-specific rules

def coerce_value(field, value, column_metadata, yaml_rules):
    """
    Returns (coerced_value, error_or_none).
    - coerced_value may be None (meaning omit / null)
    - error_or_none is a string if in strict mode you'd want to reject the row
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        value = value.strip()

    # NA/null handling
    if value == "" or _is_null_token(value, yaml_rules):
        return None, None

    dtype, field_rules = _effective_datatype(field, column_metadata, yaml_rules)
    errors = None
    try:
        if dtype in ('integer', 'long'):
            try:
                iv = int(value)
            except Exception:
                if yaml_rules.get('coercions', {}).get('integer', {}).get('drop_invalid', True):
                    return None, f"{field}: expected integer, got '{value}'"
                raise
            # bounds
            if 'min' in field_rules and iv < field_rules['min']:
                return None, f"{field}: {iv} < min {field_rules['min']}"
            if 'max' in field_rules and iv > field_rules['max']:
                return None, f"{field}: {iv} > max {field_rules['max']}"
            return iv, None

        elif dtype in ('float', 'double', 'scaled_float'):
            try:
                fv = float(value)
            except Exception:
                if yaml_rules.get('coercions', {}).get('float', {}).get('drop_invalid', True):
                    return None, f"{field}: expected float, got '{value}'"
                raise
            if 'min' in field_rules and fv < field_rules['min']:
                return None, f"{field}: {fv} < min {field_rules['min']}"
            if 'max' in field_rules and fv > field_rules['max']:
                return None, f"{field}: {fv} > max {field_rules['max']}"
            return fv, None

        elif dtype in ('boolean', 'bool'):
            try:
                bv = _parse_bool(value, yaml_rules)
            except Exception as e:
                if yaml_rules.get('coercions', {}).get('boolean', {}).get('drop_invalid', True):
                    return None, f"{field}: {e}"
                raise
            return bv, None

        elif dtype in ('date',):
            try:
                dv = _parse_date(value, field_rules, yaml_rules)
            except Exception as e:
                if yaml_rules.get('coercions', {}).get('date', {}).get('drop_invalid', True):
                    return None, f"{field}: {e}"
                raise
            return dv, None

        elif dtype in ('keyword', 'text'):
            # strings: apply trait map if this is 'trait'
            if field == 'trait':
                # trait mapping was already applied in your row-transformer, so nothing here
                pass
            return value, None

        elif dtype == 'geo_point':
            # Expect "lat,lon" or dict. If not valid, null it.
            if isinstance(value, str) and ',' in value:
                lat, lon = value.split(',', 1)
                return {'lat': float(lat.strip()), 'lon': float(lon.strip())}, None
            return None, f"{field}: invalid geo_point '{value}'"

        else:
            # Unknown: pass-through as text
            return value, None

    except Exception as e:
        # Any unexpected error -> null with reason
        return None, f"{field}: coercion exception {e}"

class ESLoader:
    def __init__(self, data_dir, index_name, drop_existing=False,
                 host='149.165.170.158', column_metadata=None, mode='machine', test_mode=False, traits_mapping=None):
        self.traits_mapping = traits_mapping or {}
        self.strict = test_mode and False  # default false unless set below


        self.host = host
        self.data_dir = data_dir
        self.index_name = index_name
        self.drop_existing = drop_existing
        self.column_metadata = column_metadata or {}
        self.system_fields = [
            field for field, meta in self.column_metadata.items()
            if meta.get('source', '').strip().lower() == 'system'
        ]
        
        self.mode = mode

        self.test_mode = test_mode;
        relevance_field = {
            'machine': 'machine_annotation_inat_relevance',
            'herbarium': 'machine_annotation_herbarium_relevance',
            'in_situ': 'machine_annotation_inat_relevance'
        }[mode]

        self.required_fields = [
            field for field, meta in self.column_metadata.items()
            if meta[relevance_field].strip().upper() == 'REQUIRED'
        ]

        if not self.test_mode:
            self.es = Elasticsearch([{'host': self.host, 'port': 8081, 'scheme': 'http'}])
            if self.es.ping():
                print(f"✅ Connected to Elasticsearch at {self.host}")
            else:
                print("❌ Could not connect to Elasticsearch.")
        else:
            print("🧪 Running in TEST mode — Elasticsearch will not be used.")

        # Load per-dataset YAML rules + optional row transformer
        transform_path = os.path.join(self.data_dir, 'transform.yaml')

        self.yaml_rules = {}
        self.transform_row = None

        if os.path.isfile(transform_path):
            # Full rules used by coercion/validation
            self.yaml_rules = load_yaml_mapping(transform_path) or {}
            # Optional row-level transform (e.g., trait_mappings)
            self.transform_row = make_row_transformer(transform_path)
            print(f"🔁 Using transform.yaml from {transform_path}")
        else:
            print("ℹ️ No transform.yaml found; proceeding without per‑dataset transforms.")
 

    def assign_system_fields(self, row, errors):
        if 'annotationID' in self.system_fields:
            row['annotationID'] = str(uuid.uuid4())

        if 'mappedTraits' in self.system_fields:
            trait_raw = row.get('trait', '').strip().lower()
            if not trait_raw:
                errors.append("Trait is empty — required for mappedTraits.")
                row['mappedTraits'] = ''
                return

            mapped = self.traits_mapping.get(trait_raw)
            if mapped is None:
                errors.append(f"Trait '{trait_raw}' not found in traits mapping.")
                row['mappedTraits'] = ''
            else:
                if isinstance(mapped, str):
                    row['mappedTraits'] = [x.strip() for x in mapped.split("|") if x.strip()]
                else:
                    row['mappedTraits'] = mapped

    def __load_file(self, file):
        count = 0
        error_count = 0
        data = []

        with open(file, encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                errors = []

                # Transform trait value if applicable
                if self.transform_row:
                    row = self.transform_row(row)

                    cleaned = {}
                    field_errors = []

                    for k, v in row.items():
                        if k in self.system_fields:
                            continue

                        coerced, err = coerce_value(k, v, self.column_metadata, self.yaml_rules)
                        if err:
                            field_errors.append(err)

                        # Store coerced value; let None through for now (we’ll prune Nones later)
                        cleaned[k] = coerced

                    # If strict mode, reject rows with any coercion error
                    if self.strict and field_errors:
                        errors.extend(field_errors)

                    # Assign system-generated fields (e.g., annotationID, mappedTraits)
                    self.assign_system_fields(row, errors)

                    # Merge system fields into cleaned row
                    for field in self.system_fields:
                        cleaned[field] = row.get(field)

                    if errors:
                        error_count += 1
                        if self.test_mode:
                            print(f"❌ Row rejected: {row.get('annotationID', 'UNKNOWN')}")
                            for err in (errors + field_errors):
                                print("   -", err)
                        self.log_error(row.get('annotationID', 'UNKNOWN'), errors + field_errors)
                    else:
                        # Prune any fields with value None
                        cleaned = {k: v for k, v in cleaned.items() if v is not None}
                        data.append(cleaned)

        if data:
            if self.test_mode:
                print("🧪 Valid rows that would be inserted:")
                for doc in data[:5]:
                    print(doc)
                if len(data) > 5:
                    print(f"... {len(data) - 5} more rows omitted.")
            else:
                print("Inserting data into ES...")
                helpers.bulk(self.es, index=self.index_name, actions=data)
            count += len(data)

        print(f"📊 Total rows read: {reader.line_num}")
        print(f"✅ Valid rows: {len(data)}")
        print(f"❌ Rows with errors: {error_count}")

        return count
 

    def load(self):
        if not self.test_mode:
            if self.drop_existing and self.es.indices.exists(index=self.index_name):
                print(f"🔁 Dropping index '{self.index_name}'")
                self.es.indices.delete(index=self.index_name)

            if not self.es.indices.exists(index=self.index_name):
                print(f"📦 Creating index '{self.index_name}'")
                self.__create_index()
        else:
            print(f"📄 Skipping index creation in test mode.")

        total_docs = 0
        for file in self.get_files(self.data_dir):
            print(f"📄 Processing file: {file}")
            total_docs += self.__load_file(file)
        print(f"✅ Would have indexed {total_docs} documents.")

    def __create_index(self):
        mapping = build_es_mapping(self.column_metadata)
        self.es.indices.create(index=self.index_name, body=mapping)


    def log_error(self, key, errors):
        with open('loading_errors.csv', 'a', newline='') as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(['annotationID', 'errors'])
            writer.writerow([key, '|'.join(errors)])

    @staticmethod
    def get_files(data_dir, ext='csv'):
        for root, _, files in os.walk(data_dir):
            for file in files:
                if file.endswith(ext):
                    yield os.path.join(root, file)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Load data into Elasticsearch.')
    parser.add_argument('data_dir', help='Directory containing CSV files to load')
    parser.add_argument('drop_existing', help='Whether to drop the existing index (True/False)')
    parser.add_argument('--mode', required=True, choices=['machine', 'in_situ', 'herbarium'], help='Relevance mode')
    parser.add_argument('--test', action='store_true', help='Run in test mode (no ES insert, just print rows)')
    parser.add_argument('--strict', action='store_true', help='Reject rows with invalid field values after coercion/validation')


    args = parser.parse_args()

    drop_existing = args.drop_existing.lower() == 'true'
    column_metadata = load_column_metadata()

    loader = ESLoader(
        data_dir=args.data_dir,
        index_name='phenobase2',
        drop_existing=drop_existing,
        host='149.165.170.158',
        column_metadata=column_metadata,
        mode=args.mode,
        test_mode=args.test,
        traits_mapping=traits_mapping
    )
    loader.strict = args.strict;
    loader.load()
