# -*- coding: utf-8 -*-
import os
import csv
import sys
import gc
import time
import warnings
import argparse
from elasticsearch import Elasticsearch, helpers
import yaml
from datetime import datetime

# Suppress all warnings including LibreSSL ones
warnings.filterwarnings("ignore")

# ----------------------------
# Metadata and ES mapping
# ----------------------------
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
        'geo_point': {'type': 'geo_point'},
        'date': {'type': 'date'}
    }
    return {
        "mappings": {
            "properties": {
                field: es_type_map.get((meta.get('datatype') or '').strip().lower(), {"type": "text"})
                for field, meta in column_metadata.items()
            }
        }
    }

# ----------------------------
# Traits mapping
# ----------------------------
def load_traits_mapping(path='data/traits.csv'):
    traits_mapping = {}
    if not os.path.exists(path):
        return traits_mapping
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            trait = (row.get('trait') or '').strip().lower()
            if trait:
                traits_mapping[trait] = row.get('mappedTraits')
    return traits_mapping

traits_mapping = load_traits_mapping()

# ----------------------------
# YAML transforms
# ----------------------------
def load_yaml_mapping(path):
    if not os.path.exists(path):
        print(f"⚠️ No transform.yaml found at {path}")
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

def make_row_transformer(transform_path):
    yaml_rules = load_yaml_mapping(transform_path)
    def transform_row(row):
        trait_val = (row.get('trait') or '').strip().lower()
        if trait_val and 'trait_mappings' in yaml_rules:
            mapped = yaml_rules['trait_mappings'].get(trait_val)
            if mapped:
                row['trait'] = mapped
        return row
    return transform_row

# ----------------------------
# Coercion helpers
# ----------------------------
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
    fld = yaml_rules.get('fields', {}).get(field, {})
    dt = (fld.get('datatype') or column_metadata.get(field, {}).get('datatype') or 'text').strip().lower()
    return dt, fld

def _apply_case(value, rule):
    if not isinstance(value, str):
        return value
    v = value.strip()
    if rule == 'capitalize_first':
        return (v[:1].upper() + v[1:].lower()) if v else v
    elif rule == 'upper':
        return v.upper()
    elif rule == 'lower':
        return v.lower()
    elif rule == 'title':
        return ' '.join(w[:1].upper() + w[1:].lower() if w else '' for w in v.split())
    elif rule == 'scientific_name_standard':
        parts = v.split()
        if not parts:
            return v
        first = parts[0][:1].upper() + parts[0][1:].lower()
        rest = [p.lower() for p in parts[1:]]
        return ' '.join([first] + rest)
    return v

def coerce_value(field, value, column_metadata, yaml_rules):
    if value is None:
        return None, None
    if isinstance(value, str):
        value = value.strip()

    if value == "" or _is_null_token(value, yaml_rules):
        return None, None

    dtype, field_rules = _effective_datatype(field, column_metadata, yaml_rules)
    try:
        if dtype in ('integer', 'long'):
            try:
                iv = int(value)
            except Exception:
                if yaml_rules.get('coercions', {}).get('integer', {}).get('drop_invalid', True):
                    return None, f"{field}: expected integer, got '{value}'"
                raise
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
            val = value
            case_rule = field_rules.get('case') or yaml_rules.get('coercions', {}).get('text', {}).get('case')
            if case_rule:
                val = _apply_case(val, case_rule)
            return val, None

        elif dtype == 'geo_point':
            if isinstance(value, str) and ',' in value:
                lat, lon = value.split(',', 1)
                try:
                    return {'lat': float(lat.strip()), 'lon': float(lon.strip())}, None
                except Exception:
                    return None, f"{field}: invalid geo_point '{value}'"
            return None, f"{field}: invalid geo_point '{value}'"

        else:
            return value, None

    except Exception as e:
        return None, f"{field}: coercion exception {e}"

# ----------------------------
# Loader
# ----------------------------
class ESLoader:
    def __init__(self, data_dir, index_name, drop_existing=False,
                 host='149.165.170.158', column_metadata=None, mode='machine',
                 test_mode=False, traits_mapping=None,
                 batch_size=5000, progress_every=50000):
        self.traits_mapping = traits_mapping or {}
        self.strict = False  # default; can be overridden by CLI

        self.host = host
        self.data_dir = data_dir
        self.index_name = index_name
        self.drop_existing = drop_existing
        self.column_metadata = column_metadata or {}
        self.system_fields = [
            field for field, meta in self.column_metadata.items()
            if (meta.get('source') or '').strip().lower() == 'system'
        ]

        self.mode = mode
        self.test_mode = test_mode

        self.batch_size = int(batch_size)
        self.progress_every = int(progress_every)

        relevance_field = {
            'machine': 'machine_annotation_inat_relevance',
            'herbarium': 'machine_annotation_herbarium_relevance',
            'in_situ': 'machine_annotation_inat_relevance'
        }[mode]

        self.required_fields = [
            field for field, meta in self.column_metadata.items()
            if (meta.get(relevance_field) or '').strip().upper() == 'REQUIRED'
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
            self.yaml_rules = load_yaml_mapping(transform_path) or {}
            self.transform_row = make_row_transformer(transform_path)
            print(f"🔁 Using transform.yaml from {transform_path}")
        else:
            print("ℹ️ No transform.yaml found; proceeding without per-dataset transforms.")

    def assign_system_fields(self, row, errors):
        """
        Enforce annotationID is present and non-empty. Do NOT generate it.
        Also compute mappedTraits if required by schema.
        """
        aid = (row.get('annotationID') or '').strip()
        if not aid:
            errors.append("Missing mandatory annotationID.")
        else:
            row['annotationID'] = aid  # normalized

        if 'mappedTraits' in self.system_fields:
            trait_raw = (row.get('trait') or '').strip().lower()
            if not trait_raw:
                errors.append("Trait is empty — required for mappedTraits.")
                row['mappedTraits'] = ''
            else:
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
        start_ts = time.time()

        # Counters
        total_read = 0
        accepted = 0
        rejected = 0
        err_docs = 0
        seen_ids = set()  # detect duplicates inside this input file only

        # Batch buffer
        batch_actions = []

        def print_progress(force=False):
            elapsed = max(time.time() - start_ts, 1e-6)
            rps = total_read / elapsed
            msg = (f"⏳ rows read={total_read:,}  accepted={accepted:,}  "
                   f"rejected={rejected:,}  errors={err_docs:,}  {rps:,.0f} rows/s")
            if force:
                print(msg)
            else:
                sys.stdout.write("\r" + msg)
                sys.stdout.flush()

        def flush_batch():
            nonlocal batch_actions, accepted, err_docs
            if not batch_actions:
                return
            if self.test_mode:
                accepted += len(batch_actions)
                batch_actions = []
                return

            try:
                for ok, item in helpers.streaming_bulk(
                    self.es,
                    actions=batch_actions,
                    chunk_size=len(batch_actions),   # already batched
                    max_retries=2,
                    request_timeout=120,
                    raise_on_error=False,
                    refresh=False
                ):
                    if ok:
                        accepted += 1
                    else:
                        err_docs += 1
            except Exception as e:
                print(f"\n❌ Bulk error on batch of {len(batch_actions)}: {e}")
                err_docs += len(batch_actions)
            finally:
                batch_actions = []
                gc.collect()

        # newline='' prevents csv module from interpreting line endings twice
        with open(file, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)

            for row in reader:
                total_read += 1
                errors = []

                # Optional row transform
                if self.transform_row:
                    row = self.transform_row(row)

                cleaned = {}
                field_errors = []

                # Coerce non-system fields
                for k, v in row.items():
                    if k in self.system_fields:
                        continue
                    coerced, err = coerce_value(k, v, self.column_metadata, self.yaml_rules)
                    if err:
                        field_errors.append(err)
                    cleaned[k] = coerced  # may be None; pruned later

                # Enforce mandatory system fields (annotationID, mappedTraits…)
                self.assign_system_fields(row, errors)

                # Always include annotationID
                aid = (row.get('annotationID') or '').strip()
                cleaned['annotationID'] = aid

                # Duplicate ID within this file?
                if aid:
                    if aid in seen_ids:
                        errors.append(f"Duplicate annotationID in input batch: {aid}")
                    else:
                        seen_ids.add(aid)

                # If strict, treat coercion problems as row errors
                if self.strict and field_errors:
                    errors.extend(field_errors)

                # Merge other system fields
                for field in self.system_fields:
                    if field == 'annotationID':
                        continue
                    cleaned[field] = row.get(field)

                if errors:
                    rejected += 1
                    if self.test_mode and rejected <= 5:
                        print(f"\n❌ Row rejected (annotationID={aid or 'UNKNOWN'}):")
                        for err in (errors + field_errors):
                            print("   -", err)
                    self.log_error(aid or 'UNKNOWN', errors + field_errors)
                else:
                    # Prune Nones
                    cleaned = {k: v for k, v in cleaned.items() if v is not None}
                    # Build ES action with idempotent _id
                    action = {
                        "_op_type": "index",        # replace if _id exists; create if not
                        "_index": self.index_name,
                        "_id": aid,
                        **cleaned
                    }
                    batch_actions.append(action)

                    # Flush a full batch
                    if len(batch_actions) >= self.batch_size:
                        flush_batch()

                # Periodic progress
                if (total_read % self.progress_every) == 0:
                    print_progress()

            # End-of-file: flush remaining
            flush_batch()
            print_progress(force=True)
            print()  # newline after the progress line

        print(f"📄 File done: {os.path.basename(file)}")
        print(f"📊 Total rows read: {total_read:,}")
        print(f"✅ Accepted (indexed or would index): {accepted:,}")
        print(f"🚫 Rejected (validation/duplicate): {rejected:,}")
        print(f"❌ ES errors during bulk: {err_docs:,}")

        return accepted

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
        if self.test_mode:
            print(f"✅ Would have indexed {total_docs:,} documents.")
        else:
            print(f"✅ Indexed {total_docs:,} documents.")

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

# ----------------------------
# CLI
# ----------------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Load data into Elasticsearch.')
    parser.add_argument('data_dir', help='Directory containing CSV files to load')
    parser.add_argument('--mode', required=True, choices=['machine', 'in_situ', 'herbarium'], help='Relevance mode')
    parser.add_argument('--test', action='store_true', help='Run in test mode (no ES insert, just print rows)')
    parser.add_argument('--strict', action='store_true', help='Reject rows with invalid field values after coercion/validation')
    parser.add_argument('--batch-size', type=int, default=5000, help='Docs per bulk request (default: 5000)')
    parser.add_argument('--progress-every', type=int, default=50000, help='Print progress every N rows (default: 50000)')
    parser.add_argument(
        '--drop-existing',
        action=argparse.BooleanOptionalAction,
        default=False,
        help='Drop the existing index before loading (default: false)'
    )

    args = parser.parse_args()
    drop_existing = args.drop_existing
    column_metadata = load_column_metadata()

    loader = ESLoader(
        data_dir=args.data_dir,
        index_name='phenobase2',
        drop_existing=drop_existing,
        host='149.165.170.158',
        column_metadata=column_metadata,
        mode=args.mode,
        test_mode=args.test,
        traits_mapping=traits_mapping,
        batch_size=args.batch_size,
        progress_every=args.progress_every
    )
    loader.strict = args.strict
    loader.load()

