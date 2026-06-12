import csv
import io
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from db import db

# sources service: uploaded CSVs live in two collections —
# `sources` holds metadata + a small sample, `source_rows` holds every row
# keyed by sourceId so large files never hit the 16MB document limit.
#
router = APIRouter()

MAX_FILE_SIZE = 25 * 1024 * 1024
ROW_BATCH = 5000
SAMPLE_SIZE = 5


def parse_csv(text):
    """Parse CSV like csvtojson({trim, ignoreEmpty}): trim headers and
    values, drop empty values, skip rows that end up empty."""
    reader = csv.DictReader(io.StringIO(text))
    columns = [name.strip() for name in (reader.fieldnames or [])]
    rows = []
    for raw in reader:
        row = {}
        for key, value in raw.items():
            if key is None or value is None:
                continue
            value = value.strip()
            if value != '':
                row[key.strip()] = value
        if row:
            rows.append(row)
    return columns, rows


# upload a csv: multipart form with `file`, plus optional folder/tier fields
#
@router.post('', status_code=201)
async def upload_source(
    file: UploadFile | None = File(None),
    folder: str = Form('Uncategorized'),
    tier: str = Form('Tier 2: Internal Operational'),
):
    if file is None:
        return JSONResponse(status_code=400, content={'message': 'No file uploaded. Send a CSV as the "file" field.'})
    raw = await file.read()
    if len(raw) > MAX_FILE_SIZE:
        return JSONResponse(status_code=413, content={'message': 'File too large (25MB max).'})

    columns, rows = parse_csv(raw.decode('utf-8', errors='replace'))
    if not rows:
        return JSONResponse(status_code=400, content={'message': 'CSV contained no data rows.'})

    source_doc = {
        'name': file.filename,
        'folder': folder,
        'tier': tier,
        'status': 'Ready',
        'confidence': 'High',
        'size': len(raw),
        'num_rows': len(rows),
        'columns': columns,
        'sample': rows[:SAMPLE_SIZE],
        'uploaded_at': datetime.now(timezone.utc),
    }
    result = await db['sources'].insert_one(source_doc)
    inserted_id = result.inserted_id

    for i in range(0, len(rows), ROW_BATCH):
        chunk = [
            {'sourceId': inserted_id, 'rowIndex': i + j, 'data': row}
            for j, row in enumerate(rows[i:i + ROW_BATCH])
        ]
        await db['source_rows'].insert_many(chunk, ordered=False)
    await db['source_rows'].create_index([('sourceId', 1), ('rowIndex', 1)])

    return {**source_doc, '_id': str(inserted_id)}


# list all sources (metadata only)
#
@router.get('')
async def list_sources():
    sources = await db['sources'].find({}).sort('uploaded_at', -1).to_list(None)
    for source in sources:
        source['_id'] = str(source['_id'])
    return sources


# rows for one source, e.g. /api/sources/:id/rows?limit=200
#
@router.get('/{source_id}/rows')
async def get_source_rows(source_id: str, limit: int = 200):
    if not ObjectId.is_valid(source_id):
        return JSONResponse(status_code=400, content={'message': 'Invalid source id.'})
    rows = await db['source_rows'] \
        .find({'sourceId': ObjectId(source_id)}) \
        .sort('rowIndex', 1) \
        .limit(min(limit, 5000)) \
        .to_list(None)
    return [row['data'] for row in rows]


# delete a source and its rows
#
@router.delete('/{source_id}')
async def delete_source(source_id: str):
    if not ObjectId.is_valid(source_id):
        return JSONResponse(status_code=400, content={'message': 'Invalid source id.'})
    result = await db['sources'].delete_one({'_id': ObjectId(source_id)})
    if result.deleted_count == 0:
        return JSONResponse(status_code=404, content={'message': 'Source not found.'})
    await db['source_rows'].delete_many({'sourceId': ObjectId(source_id)})
    return {'deleted': True}
