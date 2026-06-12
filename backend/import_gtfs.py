import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# python rewrite of import-gtfs.js: load the GTFS text files in
# google_transit/ into MongoDB collections and create the indexes the
# API queries rely on.
#
load_dotenv()

GTFS_DIR = Path(__file__).resolve().parent / 'google_transit'
BATCH_SIZE = 5000

# when run on the host (outside docker), fall back to the compose mongo port
#
MONGO_URI = os.environ.get('MONGO_URI') \
    or 'mongodb://via:via_dev_password@localhost:27017/viadata?authSource=admin'

# fields to cast per file; everything else stays a string so ids like
# stop_id and trip_id join cleanly across collections
#
FILES = {
    'agency.txt': {'collection': 'agency', 'floats': [], 'ints': []},
    'calendar.txt': {
        'collection': 'calendar',
        'floats': [],
        'ints': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'],
    },
    'calendar_dates.txt': {'collection': 'calendar_dates', 'floats': [], 'ints': ['exception_type']},
    'feed_info.txt': {'collection': 'feed_info', 'floats': [], 'ints': []},
    'routes.txt': {'collection': 'routes', 'floats': [], 'ints': ['route_type']},
    'shapes.txt': {
        'collection': 'shapes',
        'floats': ['shape_pt_lat', 'shape_pt_lon', 'shape_dist_traveled'],
        'ints': ['shape_pt_sequence'],
    },
    'stop_times.txt': {
        'collection': 'stop_times',
        'floats': ['shape_dist_traveled'],
        'ints': ['stop_sequence', 'pickup_type', 'drop_off_type', 'timepoint'],
    },
    'stops.txt': {
        'collection': 'stops',
        'floats': ['stop_lat', 'stop_lon'],
        'ints': ['location_type', 'wheelchair_boarding'],
    },
    'transfers.txt': {'collection': 'transfers', 'floats': [], 'ints': ['transfer_type', 'min_transfer_time']},
    'trips.txt': {
        'collection': 'trips',
        'floats': [],
        'ints': ['direction_id', 'wheelchair_accessible', 'bikes_allowed'],
    },
}


def cast_row(row, spec):
    for field in spec['floats']:
        if field in row:
            try:
                row[field] = float(row[field])
            except ValueError:
                pass
    for field in spec['ints']:
        if field in row:
            try:
                row[field] = int(row[field])
            except ValueError:
                pass
    return row


def read_rows(file_name):
    """Yield trimmed rows with empty values dropped, like csvtojson
    with {trim, ignoreEmpty} in the node importer."""
    with open(GTFS_DIR / file_name, encoding='utf-8-sig', newline='') as f:
        for raw in csv.DictReader(f):
            row = {}
            for key, value in raw.items():
                if key is None or value is None:
                    continue
                value = value.strip()
                if value != '':
                    row[key.strip()] = value
            if row:
                yield row


def import_file(db, file_name, spec):
    coll = db[spec['collection']]
    coll.delete_many({})

    batch = []
    total = 0
    for row in read_rows(file_name):
        cast_row(row, spec)

        # GeoJSON point so stops support $near queries
        #
        if spec['collection'] == 'stops' and 'stop_lat' in row and 'stop_lon' in row:
            row['location'] = {'type': 'Point', 'coordinates': [row['stop_lon'], row['stop_lat']]}

        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            coll.insert_many(batch, ordered=False)
            total += len(batch)
            batch = []

    if batch:
        coll.insert_many(batch, ordered=False)
        total += len(batch)
    print(f'{spec["collection"]}: imported {total} documents')


def create_indexes(db):
    db['stops'].create_index([('stop_id', 1)], unique=True)
    db['stops'].create_index([('location', '2dsphere')])
    db['routes'].create_index([('route_id', 1)], unique=True)
    db['trips'].create_index([('trip_id', 1)], unique=True)
    db['trips'].create_index([('route_id', 1)])
    db['stop_times'].create_index([('trip_id', 1), ('stop_sequence', 1)])
    db['stop_times'].create_index([('stop_id', 1)])
    db['shapes'].create_index([('shape_id', 1), ('shape_pt_sequence', 1)])
    db['calendar'].create_index([('service_id', 1)])
    db['calendar_dates'].create_index([('service_id', 1)])
    print('indexes created')


def main():
    client = MongoClient(MONGO_URI)
    client.admin.command('ping')
    print('MongoDB Connected')
    db = client.get_default_database('viadata')

    for file_name, spec in FILES.items():
        import_file(db, file_name, spec)

    create_indexes(db)
    client.close()
    print('GTFS import complete')


if __name__ == '__main__':
    try:
        main()
    except Exception as err:
        print(f'GTFS import failed: {err}', file=sys.stderr)
        sys.exit(1)
