# -*- coding: utf-8 -*-
import csv
import datetime
import json
import os,ssl
import urllib.request
import argparse
import datetime


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

    def handle_bulk_errors(self,response):
        errors = []
        for item in response['items']:
            if 'error' in item['index']:
                error_info = item['index']['error']
                error_msg = f"ID: {item['index']['_id']} - Error Type: {error_info['type']} - Reason: {error_info['reason']}"
                errors.append(error_msg)
        return errors

    def backup_existing_log():
        if os.path.exists('loading_errors.csv'):
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            os.rename('loading_errors.csv', f'loading_errors.csv.{timestamp}')

    def log_error(self,guid, errors, is_first_write=False):
        file_exists = os.path.isfile('loading_errors.csv')

        with open('loading_errors.csv', 'a', newline='') as f:
            writer = csv.writer(f)

            # Write header if file is being written for the first time
            if is_first_write or not file_exists:
                writer.writerow(['machine_learning_annotation_id', 'errors'])

            # Write error entry
            writer.writerow([guid, '|'.join(errors)])

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
        try:
            self.es = Elasticsearch(
                [{'host': self.host, 'port': 80, 'scheme': 'http'}]
            )

            # Test the connection
            if self.es.ping():
                print("Connected to Elasticsearch at {}:{}".format(self.host, 80))
            # Further operations can be performed here
            else:
                print("Could not connect to Elasticsearch.")
        except Exception as e:
            print(f"Error connecting to Elasticsearch: {e}")

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
                print(file)
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

    def is_valid_lat_lon(self, value, min_value, max_value):
        try:
            num = float(value)
            if min_value <= num <= max_value:
                return True
            else:
                return False
        except ValueError:
            return False

    # return trait mapping from trait
    def get_mapped_traits(self, traits_mapping, search_trait):
        return traits_mapping.get(search_trait)

    def is_integer(self, value):
        """Check if the given value can be converted to an integer or should be treated as blank."""
        if value.lower() == 'na':
            return True, ''  # Treat 'na' as a blank string
        try:
            int(value)
            return True, value
        except ValueError:
            return False, value:

    # Load the CSV file into a dictionary once
    csv_file_path = 'data/traits.csv'  # Replace with the path to your CSV file
    traits_mapping = load_traits_mapping(csv_file_path)
    backup_existing_log()

    def __load_file(self, file):
        doc_count = 0
        data = []

        with open(file) as f:
            print("Starting indexing on " + f.name)
            reader = csv.DictReader(f)

            for row in reader:
                errors = []

                # handle machine_learning_annotation_id
                machine_learning_annotation_id = row['machine_learning_annotation_id']
                # try annotation_id as an alias
                if not machine_learning_annotation_id:
                    machine_learning_annotation_id = row['annotation_id']

                # other fields
                if row['prediction_class'].lower() != 'detected':
                    errors.append("prediction class must be 'detected'")

                # other fields
                if row['certainty'].lower() != 'high':
                    errors.append("certainty must be set to 'high' to load")

                # Validate coordinate_uncertainty_meters as integer or convert 'na' to blank
                is_valid, row['coordinate_uncertainty_meters'] = self.is_integer(row['coordinate_uncertainty_meters'])
                if not is_valid:
                    errors.append("Field 'coordinate_uncertainty_meters' must be an integer or empty")


                # Validate year as integer
                is_valid, row['year'] = self.is_integer(row['year'])
                if not is_valid:
                    errors.append("Field 'year' must be an integer or empty")

                # Validate coordinate_uncertainty_meters as integer
                is_valid, row['day_of_year'] = self.is_integer(row['day_of_year'])
                if not is_valid:
                    errors.append("Field 'day_of_year' must be an integer or empty")

                # handle traits
                # here we convert our understanding trait + certainty to a statement of 'flowers present'
                trait = row['trait'] + "s "
                if row['certainty'] == 'low':
                    trait += 'absent'
                else:
                    trait += 'present'

                all_traits = self.get_mapped_traits(self.traits_mapping, trait)
                mapped_traits = []
                if all_traits is not None and isinstance(all_traits, str):
                    for trait in all_traits.split("|"):
                        try:
                            mapped_traits.append(trait)
                        except:
                            errors.append(f"unable to map trait: {e}")
                else:
                    errors.append("trait is None or not a string")

                row['mapped_traits'] = mapped_traits

                # handle integers

                # handle locations
                if  (row['latitude'] == '' or row['longitude'] == '' or not self.is_valid_lat_lon(row['latitude'], -90, 90) or not self.is_valid_lat_lon(row['longitude'], -180, 180)):
                    row['location'] = ''
                    row['latitude'] = ''
                    row['longitude'] = ''
                    errors.append("Invalid or empty latitude/longitude")
                else:
                    row['location'] = row['latitude'] + "," + row['longitude']

                # log an error if i can't load it, other wise append
                if errors:
                    self.log_error(machine_learning_annotation_id, errors)
                else: 
                    data.append({k: v for k, v in row.items() if v})  # remove any empty values

            print("loading now")

 #           elasticsearch.helpers.bulk(client=self.es, index=self.index_name, actions=data, raise_on_error=True, chunk_size=10000, request_timeout=60)
            try:
                   response = elasticsearch.helpers.bulk(
                       client=self.es,
                       index=self.index_name,
                       actions=data,
                       raise_on_error=False,  # Set to False to handle errors manually
                       chunk_size=10000,
                       request_timeout=60
                   )
                   print("response")
                   print(response)
                   
                   # Extract errors from the response
                   errors = self.handle_bulk_errors(response)

                   if errors:
                       log_error('bulk_insert', errors, is_first_write)
            except Exception as e:
                error_message = f"Bulk insert exception: {e}"

            doc_count += len(data)
            print("Indexed {} documents in {}".format(doc_count, f.name))

        return doc_count

    def __create_index(self):
        request_body = {
            "mappings": {
                    "properties": {
                        "machine_learning_annotation_id": { "type": "text"},
                        "datasource": {"type": "keyword"},
                        "verbatim_date": {"type": "text"},
                        "day_of_year": {"type": "integer"},
                        "year": {"type": "integer"},
                        "latitude": { "type": "float" },
                        "longitude": { "type": "float" },
                        "location": { "type": "geo_point" },       
                        "coordinate_uncertainty_meters": {"type": "integer"},
                        "family": {"type": "keyword"},
                        "scientific_name": {"type": "text"},
                        "taxon_rank": {"type": "text"},
                        "basis_of_record": {"type": "keyword"},
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

data_dir = '/home/exouser/code/phenobase_data/data/iNaturalist'
index = 'phenobase'
alias = 'phenobase'
host =  'tarly.cyverse.org'

if project is not None and drop_existing is not None:
    loader = ESLoader(data_dir, index, drop_existing, alias, host)
    loader.load()
