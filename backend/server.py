from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent import router as agent_router
from db import client, db
from sources import router as sources_router
from stats import get_departures_by_hour, get_stats, get_trips_per_route

# python rewrite of the node backend (server.js). Same routes, same response
# shapes — the frontend reads errors from body.message, so the handlers below
# keep that contract instead of FastAPI's default {"detail": ...}.
#
app = FastAPI(title='VIA MVP Backend')

# middleware to communicate with frontend
#
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://localhost:3000'],
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={'message': str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={'message': str(exc.errors()[0].get('msg', 'Invalid request'))})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={'message': str(exc)})


# test route
#
@app.get('/')
async def root():
    return PlainTextResponse('VIA MVP Backend is running smoothly!')


# health check endpoint
#
@app.get('/health')
async def health():
    try:
        await client.admin.command('ping')
        db_status = 'Connected'
    except Exception:
        db_status = 'Disconnected'
    return {'status': 'OK', 'database': db_status}


# uploaded csv sources + buffi agent
#
app.include_router(sources_router, prefix='/api/sources')
app.include_router(agent_router, prefix='/api/agent')


# gtfs data routes
# collections were loaded by import_gtfs.py, so query them directly
#

# all bus routes, e.g. for a route picker
#
@app.get('/api/routes')
async def get_routes():
    return await db['routes'] \
        .find({}, {'_id': 0}) \
        .sort('route_short_name', 1) \
        .to_list(None)


# stops nearest to a lat/lon, e.g. /api/stops/near?lat=29.4241&lon=-98.4936
#
@app.get('/api/stops/near')
async def stops_near(lat: float | None = None, lon: float | None = None, limit: int = 10):
    if lat is None or lon is None:
        return JSONResponse(status_code=400, content={'message': 'lat and lon query params are required'})
    return await db['stops'] \
        .find(
            {'location': {'$near': {'$geometry': {'type': 'Point', 'coordinates': [lon, lat]}}}},
            {'_id': 0, 'location': 0},
        ) \
        .limit(min(limit, 50)) \
        .to_list(None)


# scheduled departures at a stop, joined with trip and route info
#
@app.get('/api/stops/{stop_id}/departures')
async def stop_departures(stop_id: str, limit: int = 20):
    cursor = await db['stop_times'].aggregate([
        {'$match': {'stop_id': stop_id}},
        {'$sort': {'departure_time': 1}},
        {'$limit': min(limit, 100)},
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
                'route_color': '$route.route_color',
            }
        },
    ])
    return await cursor.to_list(None)


# dashboard stats: headline counts + feed info
# query logic lives in stats.py, shared with the buffi agent tools
#
@app.get('/api/stats')
async def stats():
    return await get_stats()


# busiest routes by number of scheduled trips
#
@app.get('/api/stats/trips-per-route')
async def stats_trips_per_route(limit: int = 10):
    return await get_trips_per_route(limit)


# system-wide departures grouped by hour of day
#
@app.get('/api/stats/departures-by-hour')
async def stats_departures_by_hour():
    return await get_departures_by_hour()
