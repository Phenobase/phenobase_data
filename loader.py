# -*- coding: utf-8 -*-
import csv
import datetime
import json
import os,ssl
import urllib.request
import argparse


import elasticsearch.helpers
from elasticsearch import Elasticsearch, serializer, compat, exceptions

if (not os.environ.get('PYTHONHTTPSVERIFY', '') and getattr(ssl, '_create_unverified_context', None)):
    ssl._create_default_https_context = ssl._create_unverified_context


TYPE = 'record'


# see https://github.com/elastic/elasticsearch-py/issues/374
class JSONSerializerPython2(serializer.JSONSerializer):
    """Override elasticsearch library serializer to ensure it encodes utf characters during json dump.
    See original at: https://github.com/elastic/elasticsearch-py/blob/master/elasticsearch/serializer.py#L42
    A description of how ensure_ascii encodes unicode characters to ensure they can be sent across the wire
    as ascii can be found here: https://docs.python.org/2/library/json.html#basic-usage
    """

    def dumps(self, data):
        # don't serialize strings
        if isinstance(data, compat.string_types):
            return data
        try:
            return json.dumps(data, default=self.default, ensure_ascii=True)
        except (ValueError, TypeError) as e:
            raise exceptions.SerializationError(data, e)


class ESLoader(object):
    def __init__(self, data_dir, index_name, drop_existing=False, alias=None, host='https://localhost:9200'):
        """
        :param data_dir
        :param index_name: the es index to upload to
        :param drop_existing:
        :param alias: the es alias to associate the index with
        """
        self.host = host
        self.data_dir = data_dir
        self.index_name = index_name
        self.drop_existing = drop_existing
        self.alias = alias
        self.es = Elasticsearch(
            [{'host': self.host, 'port': 80, 'scheme': 'http'}]
        )
    def load(self):
        if not self.es.indices.exists(index=self.index_name):
            print('creating index ' + self.index_name)
            self.__create_index()
        elif self.drop_existing:
            print('deleting index ' + self.index_name)
            self.es.indices.delete(index=self.index_name)
            print('creating index ' + self.index_name)
            self.__create_index()

        print('indexing ' + self.data_dir)

        doc_count = 0

        for file in get_files(self.data_dir):
            try:
                doc_count += self.__load_file(file)
            except RuntimeError as e:
                print(e)
                print("Failed to load file {}".format(file))

        print("Indexed {} documents total".format(doc_count))

    def __create_index(self):
        # Implement your index creation logic here
        pass

    def __load_file(self, file):
        # Implement your file loading logic here
        return 1  # Return the number of documents indexed


    # load trait mapping file
    def load_traits_mapping(csv_file_path):
        traits_mapping = {}
        with open(csv_file_path, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                traits_mapping[row['trait']] = row['mapped_traits']
        return traits_mapping

    # return trait mapping from trait
    def get_mapped_traits(traits_mapping, search_trait):
        return traits_mapping.get(search_trait)

    # Load the CSV file into a dictionary once
    csv_file_path = 'data/traits.csv'  # Replace with the path to your CSV file
    traits_mapping = load_traits_mapping(csv_file_path)

    def __load_file(self, file):
        doc_count = 0
        data = []

        with open(file) as f:
            print("Starting indexing on " + f.name)
            reader = csv.DictReader(f)

            for row in reader:
                # lookup traits
                all_traits = self.get_mapped_traits(self.traits_mapping, row['trait'])

                mapped_traits = []
                for trait in all_traits.split("|"):
                    try:
                        mapped_traits.append(self.lookup[trait])
                    except:
                        pass
                row['mapped_traits'] = mapped_traits

                # gracefully handle empty locations
                if  (row['latitude'] == '' or row['longitude'] == ''):
                    row['location'] = ''
                else:
                    row['location'] = row['latitude'] + "," + row['longitude']

                data.append({k: v for k, v in row.items() if v})  # remove any empty values

            print(json.dumps(data, indent=2))

            #elasticsearch.helpers.bulk(client=self.es, index=self.index_name, actions=data, raise_on_error=True, chunk_size=10000, request_timeout=60)
            doc_count += len(data)
            print("Indexed {} documents in {}".format(doc_count, f.name))

        return doc_count

    def __create_index(self):
        request_body = {
            "mappings": {
                    "properties": {
                        "guid": { "type": "TEXT"},
                        "datasource": {"type": "keyword"},
                        "verbatim_date": {"type": "text"},
                        "day_of_year": {"type": "integer"},
                        "year": {"type": "integer"},
                         "latitude": { "type": "float" },
                        "longitude": { "type": "float" },
                        "location": { "type": "geo_point" },       
                        "coordinate_uncertainty_meters": {"type": "integer"},
                        "family": {"type": "text"},
                        "scientific_name": {"type": "text"},
                        "taxon_rank": {"type": "text"},
                        "basis_of_record": {"type": "text"},
                        "individualID": {"type": "text"},
                        "occurrenceID": {"type": "text"},
                        "verbatim_trait": {"type": "text"},
                        "trait": {"type": "text"},
                        "mapped_traits": {"type": "keyword"},                        
                        "observed_image_guid": {"type": "text"},
                        "observed_image_url": {"type": "text"},
                        "certainty": {"type": "keyword"},                                                           
                        "model_uri": {"type": "text"},
                        "error_message": {"type": "text"}
                    }
            }
        }
        self.es.indices.create(index=self.index_name, body=request_body)

def get_files(dir, ext='csv'):
    for root, dirs, files in os.walk(dir):

        if len(files) == 0:
            print("no files found in {}".format(dir))

        for file in files:
            if file.endswith(ext):
                yield os.path.join(root, file)

parser = argparse.ArgumentParser(description='Load ES data.')
parser.add_argument('project')
parser.add_argument('drop_existing', default=False, help="Drop index before proceeding")

args = parser.parse_args()
project = args.project
drop_existing= args.drop_existing

data_dir = '/home/exouser/data/phenobase_data/sample'
index = 'phenobase'
alias = 'phenobase'
host =  'tarly.cyverse.org:80'

if project is not None and drop_existing is not None:
    loader = ESLoader(data_dir, index, drop_existing, alias, host)
    loader.load()
