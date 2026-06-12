// Buffi agent service: chat goes through the backend Agent API
// (backend/agent.js), which lets the model query MongoDB sources and
// VIA GTFS data with tools. The user's OpenAI key from Settings is
// forwarded per-request and never stored server-side.
//
// When no backend is reachable (e.g. the static GitHub Pages deploy), the
// agent runs directly in the browser instead: the same tool-calling loop
// against OpenAI (runBrowserAgent), with data tools that read the live REST
// endpoints when available and otherwise the static stats snapshot generated
// by backend/export-static-stats.js. Callers can pass extra client-side tools
// to runBrowserAgent (e.g. the Via dashboard's update_dashboard control).
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
    // agent over the snapshot-backed data tools.
    return runBrowserAgent({ userMessage, history, signal });
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.message || `Agent request failed: ${res.status}`);
  }
  const data = await res.json();
  return data.reply || '(Empty response.)';
}

// --- in-browser agent -------------------------------------------------------

const DEFAULT_SYSTEM_PROMPT = [
  'You are Buffi, a helpful data assistant for the VIA MVP platform.',
  'You can query VIA Metropolitan Transit (San Antonio) data via tools.',
  'The data is the GTFS schedule — network-wide stats, busiest routes, and',
  'departures by hour. Per-stop or per-trip detail and uploaded CSV sources',
  'are not available in this deployment; if asked for those, say so.',
  'Call tools to look at real data before answering; never invent values.',
  'Use Markdown. Be concise.',
].join(' ');

const snapshotCache = new Map();

async function getSnapshotJson(file) {
  if (!snapshotCache.has(file)) {
    const res = await fetch(`${SNAPSHOT_BASE}/${file}`);
    if (!res.ok) throw new Error(`Snapshot request failed: ${res.status}`);
    snapshotCache.set(file, await res.json());
  }
  return snapshotCache.get(file);
}

// Live backend first, static snapshot second — same data either way, so the
// agent behaves identically in docker-compose and on GitHub Pages.
async function getDataJson(apiPath, snapshotFile) {
  try {
    const res = await fetch(`${API_BASE}${apiPath}`);
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return await res.json();
  } catch (err) {
    if (err?.name === 'AbortError') throw err;
    return getSnapshotJson(snapshotFile);
  }
}

// Tool shape: { definition: <OpenAI tool definition>, run(args) → result }.
export const VIA_DATA_TOOLS = [
  {
    definition: {
      type: 'function',
      function: {
        name: 'get_stats',
        description: 'Overall VIA network stats: counts of routes, stops, scheduled trips, and stop departures, plus GTFS feed version and validity dates.',
        parameters: { type: 'object', properties: {} },
      },
    },
    run: () => getDataJson('/api/stats', 'stats.json'),
  },
  {
    definition: {
      type: 'function',
      function: {
        name: 'get_trips_per_route',
        description: 'The busiest VIA routes by number of scheduled trips, with route names (top 50).',
        parameters: { type: 'object', properties: {} },
      },
    },
    run: () => getDataJson('/api/stats/trips-per-route?limit=50', 'trips-per-route.json'),
  },
  {
    definition: {
      type: 'function',
      function: {
        name: 'get_departures_by_hour',
        description: 'Total scheduled stop departures across the VIA network for each hour of the day.',
        parameters: { type: 'object', properties: {} },
      },
    },
    run: () => getDataJson('/api/stats/departures-by-hour', 'departures-by-hour.json'),
  },
];

async function callOpenAI(apiKey, messages, toolDefinitions, signal) {
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
      tools: toolDefinitions,
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

export async function runBrowserAgent({
  userMessage,
  history = [],
  signal,
  systemPrompt = DEFAULT_SYSTEM_PROMPT,
  tools = VIA_DATA_TOOLS,
}) {
  const apiKey = getStoredApiKey();
  if (!apiKey) {
    throw new Error('No API key set. Add your OpenAI API key in Settings.');
  }
  const toolByName = new Map(tools.map((t) => [t.definition.function.name, t]));

  const messages = [{ role: 'system', content: systemPrompt }];
  for (const m of history) {
    if (!m || !m.text) continue;
    messages.push({ role: m.from === 'user' ? 'user' : 'assistant', content: m.text });
  }
  messages.push({ role: 'user', content: userMessage });

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const reply = await callOpenAI(apiKey, messages, tools.map((t) => t.definition), signal);
    if (!reply) throw new Error('OpenAI returned an empty response.');
    if (!reply.tool_calls || reply.tool_calls.length === 0) {
      return reply.content || '(Empty response.)';
    }

    messages.push(reply);
    for (const call of reply.tool_calls) {
      const tool = toolByName.get(call.function.name);
      let result;
      if (!tool) {
        result = { error: `Unknown tool: ${call.function.name}` };
      } else {
        try {
          result = await tool.run(JSON.parse(call.function.arguments || '{}'));
        } catch (err) {
          if (err?.name === 'AbortError') throw err;
          result = { error: err.message };
        }
      }
      messages.push({
        role: 'tool',
        tool_call_id: call.id,
        content: JSON.stringify(result),
      });
    }
  }
  throw new Error('Agent exceeded the tool-call limit without producing an answer.');
}
