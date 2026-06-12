import json
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import db

# agent api: POST /api/agent/chat runs Buffi as a tool-calling agent.
# The model decides which MongoDB-backed tools to call (uploaded sources
# and VIA GTFS data), we execute them, and loop until it produces an answer.
#
router = APIRouter()

OPENAI_URL = 'https://api.openai.com/v1/chat/completions'
DEFAULT_MODEL = 'gpt-5-mini'
MAX_TOOL_ROUNDS = 6
MAX_ROWS = 200

SYSTEM_PROMPT = ' '.join([
    'You are Buffi, a helpful data assistant for the VIA MVP platform.',
    'You can query two kinds of data via tools:',
    '1. CSV sources the user uploaded (list_sources, get_source_rows).',
    '2. VIA Metropolitan Transit GTFS data for San Antonio (find_nearby_stops, get_stop_departures).',
    'Call tools to look at real data before answering; never invent values.',
    'If the answer requires data that is not available, say so.',
    'Use Markdown. Be concise.',
])

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_sources',
            'description': 'List the CSV sources the user has uploaded: name, folder, columns, and row counts.',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_source_rows',
            'description': 'Fetch rows from an uploaded CSV source by its exact name (as returned by list_sources).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'source_name': {'type': 'string', 'description': 'Exact file name of the source.'},
                    'limit': {'type': 'integer', 'description': f'Max rows to return (default 100, max {MAX_ROWS}).'},
                },
                'required': ['source_name'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'find_nearby_stops',
            'description': 'Find VIA bus stops nearest to a latitude/longitude in San Antonio.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'lat': {'type': 'number'},
                    'lon': {'type': 'number'},
                    'limit': {'type': 'integer', 'description': 'Max stops to return (default 5).'},
                },
                'required': ['lat', 'lon'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_stop_departures',
            'description': 'Scheduled departures at a VIA bus stop (by stop_id), with route and headsign.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'stop_id': {'type': 'string'},
                    'limit': {'type': 'integer', 'description': 'Max departures to return (default 10).'},
                },
                'required': ['stop_id'],
            },
        },
    },
]


async def run_tool(name, args):
    if name == 'list_sources':
        return await db['sources'] \
            .find({}, {'_id': 0, 'name': 1, 'folder': 1, 'tier': 1, 'num_rows': 1, 'columns': 1}) \
            .sort('uploaded_at', -1) \
            .to_list(None)

    if name == 'get_source_rows':
        limit = min(args.get('limit') or 100, MAX_ROWS)
        source = await db['sources'].find_one({'name': args.get('source_name')})
        if not source:
            return {'error': f'No source named "{args.get("source_name")}". Use list_sources for available names.'}
        rows = await db['source_rows'] \
            .find({'sourceId': source['_id']}) \
            .sort('rowIndex', 1) \
            .limit(limit) \
            .to_list(None)
        return {
            'name': source['name'],
            'total_rows': source['num_rows'],
            'returned': len(rows),
            'rows': [row['data'] for row in rows],
        }

    if name == 'find_nearby_stops':
        return await db['stops'] \
            .find(
                {'location': {'$near': {'$geometry': {'type': 'Point', 'coordinates': [args['lon'], args['lat']]}}}},
                {'_id': 0, 'stop_id': 1, 'stop_name': 1, 'stop_lat': 1, 'stop_lon': 1},
            ) \
            .limit(min(args.get('limit') or 5, 25)) \
            .to_list(None)

    if name == 'get_stop_departures':
        cursor = await db['stop_times'].aggregate([
            {'$match': {'stop_id': str(args.get('stop_id'))}},
            {'$sort': {'departure_time': 1}},
            {'$limit': min(args.get('limit') or 10, 50)},
            {'$lookup': {'from': 'trips', 'localField': 'trip_id', 'foreignField': 'trip_id', 'as': 'trip'}},
            {'$unwind': '$trip'},
            {'$lookup': {'from': 'routes', 'localField': 'trip.route_id', 'foreignField': 'route_id', 'as': 'route'}},
            {'$unwind': '$route'},
            {
                '$project': {
                    '_id': 0,
                    'departure_time': 1,
                    'trip_headsign': '$trip.trip_headsign',
                    'route_short_name': '$route.route_short_name',
                    'route_long_name': '$route.route_long_name',
                }
            },
        ])
        return await cursor.to_list(None)

    return {'error': f'Unknown tool: {name}'}


class OpenAIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


async def call_openai(api_key, model, messages):
    async with httpx.AsyncClient(timeout=120) as http:
        res = await http.post(
            OPENAI_URL,
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
            json={'model': model, 'messages': messages, 'tools': TOOLS},
        )
    if res.status_code != 200:
        detail = ''
        try:
            detail = (res.json().get('error') or {}).get('message') or ''
        except Exception:
            pass
        raise OpenAIError(
            401 if res.status_code == 401 else 502,
            f'OpenAI {res.status_code}: {detail[:200] or "request failed"}',
        )
    data = res.json()
    choices = data.get('choices') or [{}]
    return choices[0].get('message')


@router.post('/chat')
async def chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    message = body.get('message')
    history = body.get('history') or []
    model = body.get('model')
    if not message or not isinstance(message, str):
        return JSONResponse(status_code=400, content={'message': 'Request body must include a "message" string.'})
    api_key = request.headers.get('x-openai-key') or os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return JSONResponse(status_code=400, content={'message': 'No API key. Add your OpenAI API key in Settings.'})

    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for m in history:
        if not isinstance(m, dict) or not m.get('text'):
            continue
        messages.append({'role': 'user' if m.get('from') == 'user' else 'assistant', 'content': m['text']})
    messages.append({'role': 'user', 'content': message})

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            reply = await call_openai(api_key, model or DEFAULT_MODEL, messages)
            if not reply:
                return JSONResponse(status_code=502, content={'message': 'OpenAI returned an empty response.'})
            tool_calls = reply.get('tool_calls')
            if not tool_calls:
                return {'reply': reply.get('content') or '(Empty response.)'}

            messages.append(reply)
            for call in tool_calls:
                try:
                    args = json.loads(call['function'].get('arguments') or '{}')
                    result = await run_tool(call['function']['name'], args)
                except Exception as tool_err:
                    result = {'error': str(tool_err)}
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call['id'],
                    'content': json.dumps(result, default=str),
                })
        return JSONResponse(status_code=502, content={'message': 'Agent exceeded the tool-call limit without producing an answer.'})
    except OpenAIError as err:
        return JSONResponse(status_code=err.status, content={'message': str(err)})
