import os

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

# shared async MongoDB client, mirroring the mongoose connection the node
# backend used. When run on the host (outside docker), fall back to the
# compose mongo port.
#
load_dotenv()

MONGO_URI = os.environ.get('MONGO_URI') \
    or 'mongodb://via:via_dev_password@localhost:27017/viadata?authSource=admin'

client = AsyncMongoClient(MONGO_URI)
db = client.get_default_database('viadata')
