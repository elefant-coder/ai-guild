// Token-usage snapshot API. Strictly numeric; the collector is the only writer.
import { SOURCE, ingestAuthorized, ownerAuthorized } from './index.js';

export const STATS_SCHEMA = `CREATE TABLE IF NOT EXISTS guild_stats (
  source TEXT PRIMARY KEY NOT NULL, collected_at TEXT NOT NULL,
  received_at TEXT NOT NULL, payload_json TEXT NOT NULL
)`;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: {
  'Content-Type': 'application/json', 'Cache-Control': 'private, no-store',
} });
const keys = (v, allowed) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).every(k => allowed.includes(k));
const number = n => Number.isSafeInteger(n) && n >= 0;
const iso = s => typeof s === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(s) && Number.isFinite(Date.parse(s));
const metrics = v => keys(v, ['input', 'output', 'cachedInput', 'total']) && [v.input, v.output, v.cachedInput, v.total].every(number) && v.cachedInput <= v.input && v.total === v.input + v.output;
const ID = /^[a-z0-9-]{1,32}$/;

function dateIn(timezone, at) {
  try { return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(at)); }
  catch { return null; }
}

export function validStats(v) {
  if (!keys(v, ['schemaVersion', 'collectedAt', 'scope', 'timezone', 'totals', 'today', 'days', 'providers', 'coverage']) || v.schemaVersion !== 1) return false;
  if (typeof v.scope !== 'string' || v.scope.length < 1 || v.scope.length > 80 || /[\u0000-\u001f]/.test(v.scope)) return false;
  if (typeof v.timezone !== 'string' || v.timezone.length > 64 || !/^[A-Za-z_+\-/0-9]+$/.test(v.timezone)) return false;
  if (!iso(v.collectedAt) || Date.parse(v.collectedAt) > Date.now() + 60000 || !metrics(v.totals) || !metrics(v.today)) return false;
  if (!Array.isArray(v.days) || v.days.length !== 7 || !v.days.every(d => keys(d, ['date', 'input', 'output', 'cachedInput', 'total']) && /^\d{4}-\d\d-\d\d$/.test(d.date) && metrics(Object.fromEntries(Object.entries(d).filter(([k]) => k !== 'date'))))) return false;
  const end = dateIn(v.timezone, v.collectedAt);
  if (!end || v.days[6].date !== end || v.days.some((d, i) => i > 0 && Date.parse(d.date) - Date.parse(v.days[i - 1].date) !== 86400000)) return false;
  if (!['input', 'output', 'cachedInput', 'total'].every(k => v.today[k] === v.days[6][k] && v.today[k] <= v.totals[k])) return false;
  if (!Array.isArray(v.providers) || v.providers.length < 1 || v.providers.length > 6 || new Set(v.providers.map(p => p.id)).size !== v.providers.length
    || !v.providers.every(p => keys(p, ['id', 'name', 'totals', 'today', 'sessions']) && typeof p.id === 'string' && ID.test(p.id) && typeof p.name === 'string' && p.name.length >= 1 && p.name.length <= 40 && metrics(p.totals) && metrics(p.today) && number(p.sessions))) return false;
  if (!['input', 'output', 'cachedInput', 'total'].every(k => v.providers.reduce((sum, p) => sum + p.totals[k], 0) === v.totals[k] && v.providers.reduce((sum, p) => sum + p.today[k], 0) === v.today[k])) return false;
  return keys(v.coverage, ['startedAt', 'notes']) && (v.coverage.startedAt === null || iso(v.coverage.startedAt)) && Array.isArray(v.coverage.notes) && v.coverage.notes.length <= 8 && v.coverage.notes.every(n => typeof n === 'string' && n.length <= 400 && !/[\u0000-\u001f]/.test(n));
}

export async function handleStatsApi(request, env, db) {
  if (new URL(request.url).pathname !== '/api/stats') return json({ error: 'not_found' }, 404);
  if (request.method === 'GET') {
    if (!ownerAuthorized(request)) return json({ error: 'forbidden' }, 403);
    const row = await db.prepare('SELECT payload_json FROM guild_stats WHERE source = ?').bind(SOURCE).first();
    return row ? json(JSON.parse(row.payload_json)) : json({ error: 'stats_unavailable' }, 404);
  }
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
  if (!ingestAuthorized(request, env)) return json({ error: 'unauthorized' }, 401);
  if (request.headers.get('Content-Type')?.split(';')[0] !== 'application/json') return json({ error: 'unsupported_media_type' }, 415);
  const declared = request.headers.get('Content-Length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > 32768)) return json({ error: 'too_large' }, 413);
  const reader = request.body?.getReader(); if (!reader) return json({ error: 'missing_body' }, 400);
  let body = '', length = 0; const decoder = new TextDecoder();
  try { while (true) { const { value, done } = await reader.read(); if (done) break; length += value.length; if (length > 32768) { await reader.cancel(); return json({ error: 'too_large' }, 413); } body += decoder.decode(value, { stream: true }); } body += decoder.decode(); } finally { reader.releaseLock(); }
  let payload; try { payload = JSON.parse(body); } catch { return json({ error: 'invalid_json' }, 400); }
  if (!validStats(payload)) return json({ error: 'invalid_payload' }, 400);
  const previous = await db.prepare('SELECT collected_at, payload_json FROM guild_stats WHERE source = ?').bind(SOURCE).first();
  if (previous && Date.parse(previous.collected_at) >= Date.parse(payload.collectedAt)) return previous.payload_json === body ? json({ accepted: true, idempotent: true }) : json({ error: 'stale_snapshot' }, 409);
  const result = await db.prepare('INSERT INTO guild_stats (source,collected_at,received_at,payload_json) VALUES (?,?,?,?) ON CONFLICT(source) DO UPDATE SET collected_at=excluded.collected_at,received_at=excluded.received_at,payload_json=excluded.payload_json WHERE guild_stats.collected_at < excluded.collected_at').bind(SOURCE, new Date(payload.collectedAt).toISOString(), new Date().toISOString(), body).run();
  return result.meta.changes === 1 ? json({ accepted: true, collectedAt: payload.collectedAt }, 202) : json({ error: 'stale_snapshot' }, 409);
}
