// Buffi agent service: chat goes through the backend Agent API
// (backend/agent.js), which lets the model query MongoDB sources and
// VIA GTFS data with tools. The user's OpenAI key from Settings is
// forwarded per-request and never stored server-side.
//
// When no backend is reachable (e.g. the static GitHub Pages deploy), the
// agent runs directly in the browser instead: the same tool-calling loop
// against OpenAI, with tools backed by the static stats snapshot generated
// by backend/export-static-stats.js.
import { API_BASE } from './api';
import { getStoredApiKey, getStoredModel } from './openai';

const OPENAI_URL = 'https://api.openai.com/v1/chat/completions';
const SNAPSHOT_BASE = `${process.env.PUBLIC_URL || ''}/data/via`;
const MAX_TOOL_ROUNDS = 6;

export async function chatWithAgent({ userMessage, history = [], signal }) {
  const apiKey = getStoredApiKey();
  if (!apiKey) {
    throw new Error('No API key set. Add your OpenAI API key in Settings.');
  }
  let res;
  try {
    res = await fetch(`${API_BASE}/api/agent/chat`, {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'x-openai-key': apiKey,
      },
      body: JSON.stringify({
        message: userMessage,
        history,
        model: getStoredModel(),
      }),
    });
  } catch (err) {
    if (err?.name === 'AbortError') throw err;
    // Network failure — backend isn't running. Fall back to the in-browser
    // agent over the static stats snapshot.
    return chatWithStaticAgent({ apiKey, userMessage, history, signal });
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.message || `Agent request failed: ${res.status}`);
  }
  const data = await res.json();
  return data.reply || '(Empty response.)';
}

// --- in-browser fallback agent (static snapshot) ---------------------------

const STATIC_SYSTEM_PROMPT = [
  'You are Buffi, a helpful data assistant for the VIA MVP platform.',
  'You can query VIA Metropolitan Transit (San Antonio) data via tools.',
  'The data is a precomputed static snapshot of the GTFS schedule — network-wide',
  'stats, busiest routes, and departures by hour. Per-stop or per-trip detail',
  'and uploaded CSV sources are not available in this deployment; if asked for',
  'those, say so.',
  'Call tools to look at real data before answering; never invent values.',
  'Use Markdown. Be concise.',
].join(' ');

const STATIC_TOOLS = [
  {
    type: 'function',
    function: {
      name: 'get_stats',
      description: 'Overall VIA network stats: counts of routes, stops, scheduled trips, and stop departures, plus GTFS feed version and validity dates.',
      parameters: { type: 'object', properties: {} },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_trips_per_route',
      description: 'The 10 busiest VIA routes by number of scheduled trips, with route names.',
      parameters: { type: 'object', properties: {} },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_departures_by_hour',
      description: 'Total scheduled stop departures across the VIA network for each hour of the day.',
      parameters: { type: 'object', properties: {} },
    },
  },
];

const SNAPSHOT_FILES = {
  get_stats: 'stats.json',
  get_trips_per_route: 'trips-per-route.json',
  get_departures_by_hour: 'departures-by-hour.json',
};

const snapshotCache = new Map();

async function runStaticTool(name) {
  const file = SNAPSHOT_FILES[name];
  if (!file) return { error: `Unknown tool: ${name}` };
  try {
    if (!snapshotCache.has(file)) {
      const res = await fetch(`${SNAPSHOT_BASE}/${file}`);
      if (!res.ok) throw new Error(`Snapshot request failed: ${res.status}`);
      snapshotCache.set(file, await res.json());
    }
    return snapshotCache.get(file);
  } catch (err) {
    return { error: `Snapshot data unavailable: ${err.message}` };
  }
}

async function callOpenAI(apiKey, messages, signal) {
  const res = await fetch(OPENAI_URL, {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: getStoredModel(),
      messages,
      tools: STATIC_TOOLS,
    }),
  });
  if (!res.ok) {
    let detail = '';
    try {
      const errBody = await res.json();
      detail = errBody?.error?.message || '';
    } catch {}
    throw new Error(`OpenAI ${res.status}: ${detail.slice(0, 200) || 'request failed'}`);
  }
  const data = await res.json();
  return data.choices?.[0]?.message;
}

async function chatWithStaticAgent({ apiKey, userMessage, history, signal }) {
  const messages = [{ role: 'system', content: STATIC_SYSTEM_PROMPT }];
  for (const m of history) {
    if (!m || !m.text) continue;
    messages.push({ role: m.from === 'user' ? 'user' : 'assistant', content: m.text });
  }
  messages.push({ role: 'user', content: userMessage });

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const reply = await callOpenAI(apiKey, messages, signal);
    if (!reply) throw new Error('OpenAI returned an empty response.');
    if (!reply.tool_calls || reply.tool_calls.length === 0) {
      return reply.content || '(Empty response.)';
    }

    messages.push(reply);
    for (const call of reply.tool_calls) {
      const result = await runStaticTool(call.function.name);
      messages.push({
        role: 'tool',
        tool_call_id: call.id,
        content: JSON.stringify(result),
      });
    }
  }
  throw new Error('Agent exceeded the tool-call limit without producing an answer.');
}
