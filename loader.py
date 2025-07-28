# -*- coding: utf-8 -*-
import os
import csv
import sys
import warnings
import argparse
from elasticsearch import Elasticsearch, helpers
import uuid
import datetime

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

class ESLoader:
    def __init__(self, data_dir, index_name, drop_existing=False,
                 host='149.165.170.158', column_metadata=None, mode='machine', test_mode=False, traits_mapping=None):
        self.traits_mapping = traits_mapping or {}

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
            'inat': 'machine_annotation_inat_relevance',
            'herbarium': 'machine_annotation_herbarium_relevance'
        }[mode]

        self.required_fields = [
            field for field, meta in self.column_metadata.items()
            if meta[relevance_field].strip().upper() == 'REQUIRED'
        ]

        if not self.test_mode:
            self.es = Elasticsearch([{'host': self.host, 'port': 9200, 'scheme': 'http'}])
            if self.es.ping():
                print(f"✅ Connected to Elasticsearch at {self.host}")
            else:
                print("❌ Could not connect to Elasticsearch.")
        else:
            print("🧪 Running in TEST mode — Elasticsearch will not be used.")


    
    import uuid

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
                row['mappedTraits'] = mapped

    def __load_file(self, file):
        count = 0
        error_count = 0
        data = []

        with open(file, encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                errors = []

                # Validate required fields (excluding system-assigned)
                for field in self.required_fields:
                    if field in self.system_fields:
                        continue
                    if not row.get(field):
                        errors.append(f"{field} is required but missing")

                # Assign system-generated fields (may add more errors)
                self.assign_system_fields(row, errors)

                if errors:
                    error_count += 1
                    if self.test_mode:
                        print(f"❌ Row rejected: {row.get('annotationID', 'UNKNOWN')}")
                        for err in errors:
                            print("   -", err)
                    self.log_error(row.get('annotationID', 'UNKNOWN'), errors)
                else:
                    cleaned = {
                        k: v for k, v in row.items()
                        if v.strip() and k not in self.system_fields
                    }
                    for field in self.system_fields:
                        cleaned[field] = row[field]
                    data.append(cleaned)

        if data:
            if self.test_mode:
                print("🧪 Valid rows that would be inserted:")
                for doc in data:
                    print(doc)
            else:
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
    parser.add_argument('--mode', required=True, choices=['machine', 'inat', 'herbarium'], help='Relevance mode')
    parser.add_argument('--test', action='store_true', help='Run in test mode (no ES insert, just print rows)')

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
    loader.load()
