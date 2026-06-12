import asyncio

from db import db

# dashboard stat queries, shared by the REST endpoints in server.py and the
# buffi agent tools in agent.py so both always see the same numbers
#


async def get_stats():
    routes, stops, trips, stop_times, shapes, sources, feed = await asyncio.gather(
        db['routes'].estimated_document_count(),
        db['stops'].estimated_document_count(),
        db['trips'].estimated_document_count(),
        db['stop_times'].estimated_document_count(),
        db['shapes'].estimated_document_count(),
        db['sources'].count_documents({}),
        db['feed_info'].find_one({}, {'_id': 0}),
    )
    return {
        'routes': routes,
        'stops': stops,
        'trips': trips,
        'stop_times': stop_times,
        'shapes': shapes,
        'sources': sources,
        'feed': feed,
    }


async def get_trips_per_route(limit=10):
    cursor = await db['trips'].aggregate([
        {'$group': {'_id': '$route_id', 'trips': {'$sum': 1}}},
        {'$sort': {'trips': -1}},
        {'$limit': min(limit, 50)},
        {'$lookup': {'from': 'routes', 'localField': '_id', 'foreignField': 'route_id', 'as': 'route'}},
        {'$unwind': '$route'},
        {
            '$project': {
                '_id': 0,
                'route_id': '$_id',
                'trips': 1,
                'route_short_name': '$route.route_short_name',
                'route_long_name': '$route.route_long_name',
                'route_color': '$route.route_color',
            }
        },
    ])
    return await cursor.to_list(None)


# gtfs times can exceed 24h for after-midnight service, so wrap with mod 24
#
async def get_departures_by_hour():
    cursor = await db['stop_times'].aggregate([
        {
            '$project': {
                'hour': {
                    '$convert': {
                        'input': {'$arrayElemAt': [{'$split': ['$departure_time', ':']}, 0]},
                        'to': 'int',
                        'onError': -1,
                        'onNull': -1,
                    }
                }
            }
        },
        {'$match': {'hour': {'$gte': 0}}},
        {'$group': {'_id': {'$mod': ['$hour', 24]}, 'departures': {'$sum': 1}}},
        {'$sort': {'_id': 1}},
        {'$project': {'_id': 0, 'hour': '$_id', 'departures': 1}},
    ])
    return await cursor.to_list(None)
