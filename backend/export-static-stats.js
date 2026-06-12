// Exports the dashboard stats endpoints (/api/stats, /api/stats/trips-per-route,
// /api/stats/departures-by-hour) as static JSON computed straight from the GTFS
// files in google_transit/, so the Via dashboard can run without the backend
// (e.g. on GitHub Pages). Mirrors the Mongo aggregations in stats.js.
//
// Usage:  node backend/export-static-stats.js
// Output: frontend/public/data/via/{stats,trips-per-route,departures-by-hour}.json

import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GTFS_DIR = path.join(__dirname, 'google_transit');
const OUT_DIR = path.join(__dirname, '..', 'frontend', 'public', 'data', 'via');

// minimal quote-aware CSV line parser (GTFS fields like route_long_name can
// contain quoted commas)
function parseCsvLine(line) {
    const fields = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (inQuotes) {
            if (ch === '"') {
                if (line[i + 1] === '"') { cur += '"'; i++; }
                else inQuotes = false;
            } else cur += ch;
        } else if (ch === '"') inQuotes = true;
        else if (ch === ',') { fields.push(cur); cur = ''; }
        else cur += ch;
    }
    fields.push(cur);
    return fields.map((f) => f.trim());
}

function readCsv(file) {
    const lines = fs.readFileSync(path.join(GTFS_DIR, file), 'utf8')
        .split(/\r?\n/).filter((l) => l.length > 0);
    const header = parseCsvLine(lines[0].replace(/^﻿/, ''));
    return lines.slice(1).map((line) => {
        const fields = parseCsvLine(line);
        return Object.fromEntries(header.map((h, i) => [h, fields[i] ?? '']));
    });
}

// stream large files line-by-line; cb gets the parsed header once, then each
// raw data line (simple split is enough for the columns these files need)
function streamCsv(file, onRow) {
    return new Promise((resolve, reject) => {
        const rl = readline.createInterface({
            input: fs.createReadStream(path.join(GTFS_DIR, file)),
            crlfDelay: Infinity,
        });
        let header = null;
        rl.on('line', (line) => {
            if (!line) return;
            if (!header) header = parseCsvLine(line.replace(/^﻿/, ''));
            else onRow(header, line);
        });
        rl.on('close', resolve);
        rl.on('error', reject);
    });
}

async function countRows(file) {
    let n = 0;
    await streamCsv(file, () => { n++; });
    return n;
}

async function main() {
    const routes = readCsv('routes.txt');
    const feedInfo = readCsv('feed_info.txt')[0] || null;

    // trips per route (top 10, like getTripsPerRoute)
    const tripCounts = new Map();
    let tripsTotal = 0;
    await streamCsv('trips.txt', (header, line) => {
        tripsTotal++;
        const routeId = line.slice(0, line.indexOf(','));
        tripCounts.set(routeId, (tripCounts.get(routeId) || 0) + 1);
    });
    const routeById = new Map(routes.map((r) => [r.route_id, r]));
    const tripsPerRoute = [...tripCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([route_id, trips]) => {
            const r = routeById.get(route_id) || {};
            return {
                route_id,
                trips,
                route_short_name: r.route_short_name || route_id,
                route_long_name: r.route_long_name || '',
                route_color: r.route_color || '',
            };
        });

    // departures by hour (like getDeparturesByHour; gtfs times can exceed 24h)
    const hours = new Array(24).fill(0);
    let stopTimesTotal = 0;
    await streamCsv('stop_times.txt', (header, line) => {
        stopTimesTotal++;
        // columns: trip_id,arrival_time,departure_time,...  (no quoted commas)
        const parts = line.split(',');
        const hour = parseInt(parts[2], 10);
        if (!Number.isNaN(hour) && hour >= 0) hours[hour % 24]++;
    });
    const departuresByHour = hours
        .map((departures, hour) => ({ hour, departures }))
        .filter((d) => d.departures > 0);

    const [stopsTotal, shapesTotal] = await Promise.all([
        countRows('stops.txt'),
        countRows('shapes.txt'),
    ]);

    const stats = {
        routes: routes.length,
        stops: stopsTotal,
        trips: tripsTotal,
        stop_times: stopTimesTotal,
        shapes: shapesTotal,
        sources: 0,
        feed: feedInfo,
    };

    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(path.join(OUT_DIR, 'stats.json'), JSON.stringify(stats));
    fs.writeFileSync(path.join(OUT_DIR, 'trips-per-route.json'), JSON.stringify(tripsPerRoute));
    fs.writeFileSync(path.join(OUT_DIR, 'departures-by-hour.json'), JSON.stringify(departuresByHour));
    console.log('Wrote static stats to', OUT_DIR);
    console.log(JSON.stringify(stats, null, 2));
}

main().catch((err) => { console.error(err); process.exit(1); });
