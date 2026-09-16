// Inventory API: validation, revision ordering and storage. Runs inside the Durable Object (see cloudflare-storage.js).
const MAX_BODY_BYTES = 1_200_000;
const MAX_ITEMS = 2_500;
const DEFAULT_STALE_AFTER_SECONDS = 180;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
export const SOURCE = 'collector';
export const OWNER_HEADER = 'x-guild-authenticated';
const MACHINE_ID = /^[a-z0-9-]{1,32}$/;
const ITEM_KEYS = new Set(['id', 'name', 'category', 'summary', 'tags', 'status', 'statusLabel', 'machines', 'runtimes', 'evidence', 'relatedIds', 'command', 'schedule', 'meta']);
const EVIDENCE_KEYS = new Set(['source', 'detail', 'checkedAt']);
const SCHEDULE_KEYS = new Set(['kind', 'label', 'intervalSeconds', 'calendar', 'loaded', 'running', 'lastExitCode']);
const SOURCE_KEYS = new Set(['name', 'collectedAt', 'count', 'scope']);
const MACHINE_KEYS = new Set(['id', 'status', 'statusLabel', 'checkedAt', 'architecture', 'os', 'jobCount', 'loadedJobCount', 'runningJobCount', 'skillCount', 'runtimes', 'reachability', 'stale', 'lastAttemptAt', 'lastSuccessfulAt']);
const META_KEYS = new Set(['title', 'strengths', 'exampleTask', 'role', 'origin', 'modelPolicy', 'variants', 'descriptionOriginal', 'descriptionOriginalVariants', 'sources', 'sourceCount', 'sourceFile', 'label', 'runAtLoad', 'event', 'hookGroups', 'provider', 'model', 'effort', 'contextWindow', 'defaultReasoning', 'executable', 'enabled', 'toolCount', 'summarySource', 'sessionAvailability', 'visibleToCli', 'rosterScope', 'schedulerId']);
const FORBIDDEN_KEY = /(?:password|secret|api.?key|access.?token|refresh.?token|authorization|credential|environment|^env$|^token$|arguments|^args$|^payload$|^message$|^body$)/i;

export const secretPatterns = [
  /sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}/,
  /xox[baprs]-[A-Za-z0-9-]{15,}/,
  /-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----/,
  /Bearer\s+[A-Za-z0-9._-]{20,}/i,
  /\bAIza[A-Za-z0-9_-]{25,}/,
  /gh[pousr]_[A-Za-z0-9]{20,}/,
  /\/Users\/[^/\s"]+/,
  /\/home\/[^/\s"]+/,
];

function json(body, status = 200, headers = {}) {
  const conflictHeaders = status === 409 && body && Number.isSafeInteger(body.currentRevision)
    ? { 'X-Current-Revision': String(body.currentRevision), ...(typeof body.currentContentHash === 'string' ? { 'X-Current-Content-Hash': body.currentContentHash } : {}) }
    : {};
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, no-store', ...conflictHeaders, ...headers },
  });
}

function now() { return new Date().toISOString(); }

function validIso(value) {
  if (typeof value !== 'string') return false;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && parsed <= Date.now() + MAX_CLOCK_SKEW_MS;
}

function allowedKeys(object, keys) {
  return object && typeof object === 'object' && !Array.isArray(object) && Object.keys(object).every(key => keys.has(key) && !FORBIDDEN_KEY.test(key));
}

function safeString(value, maximum = 4_000) { return typeof value === 'string' && value.length <= maximum; }

function safeValue(value, depth = 0) {
  if (depth > 4) return false;
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value === 'string') return value.length <= 4_000;
  if (Array.isArray(value)) return value.length <= 200 && value.every(entry => safeValue(entry, depth + 1));
  if (!value || typeof value !== 'object' || Object.keys(value).length > 80) return false;
  return Object.entries(value).every(([key, child]) => !FORBIDDEN_KEY.test(key) && safeValue(child, depth + 1));
}

function validateItem(item) {
  if (!allowedKeys(item, ITEM_KEYS)) return false;
  if (!safeString(item.id, 160) || !/^[A-Za-z0-9@-]+$/.test(item.id) || !safeString(item.name, 400) || !safeString(item.summary, 4_000)) return false;
  if (!['model', 'cli', 'connection', 'agent', 'harness', 'skill', 'automation'].includes(item.category) || !['observed', 'configured', 'attention', 'unknown'].includes(item.status) || !safeString(item.statusLabel, 500)) return false;
  if (!Array.isArray(item.tags) || item.tags.length > 50 || !item.tags.every(tag => safeString(tag, 120)) || !Array.isArray(item.machines) || item.machines.length > 20 || !item.machines.every(machine => typeof machine === 'string' && MACHINE_ID.test(machine)) || !Array.isArray(item.runtimes) || !item.runtimes.every(runtime => safeString(runtime, 120))) return false;
  if (!Array.isArray(item.evidence) || !item.evidence.length || item.evidence.length > 50 || !item.evidence.every(evidence => allowedKeys(evidence, EVIDENCE_KEYS) && safeString(evidence.source, 500) && safeString(evidence.detail, 1_000) && validIso(evidence.checkedAt))) return false;
  if (item.relatedIds && (!Array.isArray(item.relatedIds) || !item.relatedIds.every(id => safeString(id, 160) && /^[A-Za-z0-9@-]+$/.test(id)))) return false;
  if (item.command && !safeString(item.command, 1_000)) return false;
  if (item.schedule && (!allowedKeys(item.schedule, SCHEDULE_KEYS) || !['calendar', 'interval', 'resident', 'ondemand', 'other'].includes(item.schedule.kind) || !safeString(item.schedule.label, 300) || ('calendar' in item.schedule && !safeValue(item.schedule.calendar)))) return false;
  return !item.meta || (allowedKeys(item.meta, META_KEYS) && Object.values(item.meta).every(value => safeValue(value)));
}

function validateFreshness(inventory) {
  if (!allowedKeys(inventory, new Set(['collectedAt', 'items', 'sources', 'machines', 'notes']))) return false;
  if (!validIso(inventory.collectedAt) || !Array.isArray(inventory.sources) || inventory.sources.length > 20 || !inventory.sources.every(source => allowedKeys(source, SOURCE_KEYS) && safeString(source.name, 200) && validIso(source.collectedAt) && Number.isInteger(source.count) && source.count >= 0 && (!source.scope || safeString(source.scope, 1_000)))) return false;
  if (!inventory.machines || typeof inventory.machines !== 'object' || Array.isArray(inventory.machines) || Object.keys(inventory.machines).length > 20 || Object.keys(inventory.machines).some(key => !MACHINE_ID.test(key))) return false;
  if (!Object.values(inventory.machines).every(machine => allowedKeys(machine, MACHINE_KEYS) && Object.values(machine).every(value => safeValue(value)))) return false;
  return Array.isArray(inventory.notes) && inventory.notes.length <= 100 && inventory.notes.every(note => safeString(note, 2_000));
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}

function semanticInventory(value) {
  if (Array.isArray(value)) return value.map(semanticInventory);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !['collectedAt', 'checkedAt', 'generatedAt', 'heartbeatAt', 'receivedAt', 'ageSeconds'].includes(key))
    .map(([key, child]) => [key, semanticInventory(child)]));
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(typeof value === 'string' ? value : JSON.stringify(value));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function secureEqual(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string' || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}

function bearer(request, headerName) {
  const value = request.headers.get(headerName) ?? '';
  return value.startsWith('Bearer ') ? value.slice(7) : '';
}

export function ingestAuthorized(request, env) {
  const expected = env.GUILD_INGEST_SECRET;
  return typeof expected === 'string' && expected.length >= 24 && secureEqual(bearer(request, 'Authorization'), expected);
}

/** The outer worker strips any incoming x-guild-* headers and sets this one only after cookie verification. */
export function ownerAuthorized(request) {
  return request.headers.get(OWNER_HEADER) === 'owner';
}

function staleAfter(env) {
  const value = Number(env.GUILD_STALE_AFTER_SECONDS);
  return Number.isInteger(value) && value >= 60 && value <= 86_400 ? value : DEFAULT_STALE_AFTER_SECONDS;
}

function syncMeta(row, staleAfterSeconds) {
  const received = Date.parse(row.received_at);
  const ageSeconds = Number.isFinite(received) ? Math.max(0, Math.floor((Date.now() - received) / 1000)) : null;
  return {
    source: row.source,
    revision: row.revision,
    contentHash: row.content_hash,
    collectedAt: row.collected_at,
    heartbeatAt: row.heartbeat_at,
    receivedAt: row.received_at,
    ageSeconds,
    stale: ageSeconds === null || ageSeconds > staleAfterSeconds,
    staleAfterSeconds,
  };
}

function freshnessMeta(row, staleAfterSeconds) {
  const payload = JSON.parse(row.payload_json);
  const inventory = payload.inventory;
  return {
    schemaVersion: 1,
    inventory: {
      collectedAt: inventory.collectedAt ?? null,
      sources: Array.isArray(inventory.sources) ? inventory.sources : [],
      machines: inventory.machines && typeof inventory.machines === 'object' ? inventory.machines : {},
    },
    sync: syncMeta(row, staleAfterSeconds),
  };
}

function validateEnvelope(payload) {
  if (!allowedKeys(payload, new Set(['schemaVersion', 'source', 'revision', 'collectedAt', 'heartbeatAt', 'inventory']))) return 'unexpected top-level key';
  if (payload.schemaVersion !== 1) return 'schemaVersion must be 1';
  if (payload.source !== SOURCE) return `source must be "${SOURCE}"`;
  if (!Number.isSafeInteger(payload.revision) || payload.revision < 1) return 'revision must be an integer >= 1';
  if (!validIso(payload.collectedAt) || !validIso(payload.heartbeatAt) || Date.parse(payload.heartbeatAt) < Date.parse(payload.collectedAt)) return 'invalid collection timestamps';
  const inventory = payload.inventory;
  if (!inventory || !Array.isArray(inventory.items) || !validateFreshness(inventory)) return 'invalid inventory metadata';
  if (inventory.items.length > MAX_ITEMS) return 'too many items';
  if (!inventory.items.every(validateItem)) return 'invalid inventory item';
  const text = JSON.stringify(payload);
  if (secretPatterns.some(pattern => pattern.test(text))) return 'payload contains a secret-looking value';
  return null;
}

async function latest(env) {
  return env.DB.prepare('SELECT source, revision, collected_at, heartbeat_at, received_at, content_hash, payload_json FROM guild_inventory WHERE source = ?')
    .bind(SOURCE).first();
}

function validateHeartbeat(payload) {
  if (!allowedKeys(payload, new Set(['schemaVersion', 'source', 'revision', 'contentHash', 'collectedAt', 'heartbeatAt', 'sources', 'machines']))) return 'unexpected top-level key';
  if (payload.schemaVersion !== 1 || payload.source !== SOURCE || !Number.isSafeInteger(payload.revision) || payload.revision < 1 || !safeString(payload.contentHash, 64) || !/^[a-f0-9]{64}$/.test(payload.contentHash)) return 'invalid heartbeat identity';
  if (!validIso(payload.collectedAt) || !validIso(payload.heartbeatAt) || Date.parse(payload.heartbeatAt) < Date.parse(payload.collectedAt)) return 'invalid heartbeat timestamps';
  const freshness = { collectedAt: payload.collectedAt, items: [], sources: payload.sources, machines: payload.machines, notes: [] };
  if (!validateFreshness(freshness)) return 'invalid heartbeat metadata';
  return secretPatterns.some(pattern => pattern.test(JSON.stringify(payload))) ? 'payload contains a secret-looking value' : null;
}

async function updateHeartbeat(env, row, merged, heartbeatAt, receivedAt) {
  const payloadJson = JSON.stringify(merged);
  return env.DB.prepare(
    'UPDATE guild_inventory SET collected_at = ?, heartbeat_at = ?, received_at = ?, payload_json = ?, payload_bytes = ? WHERE source = ? AND revision = ? AND content_hash = ? AND heartbeat_at <= ?'
  ).bind(merged.collectedAt, heartbeatAt, receivedAt, payloadJson, new TextEncoder().encode(payloadJson).byteLength, row.source, row.revision, row.content_hash, heartbeatAt).run();
}

async function postInventory(request, env) {
  if (!ingestAuthorized(request, env)) return json({ error: 'unauthorized' }, 401);
  const length = Number(request.headers.get('Content-Length'));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) return json({ error: 'payload_too_large' }, 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return json({ error: 'payload_too_large' }, 413);
  let payload;
  try { payload = JSON.parse(raw); } catch { return json({ error: 'invalid_json' }, 400); }
  const invalid = validateEnvelope(payload);
  if (invalid) return json({ error: 'invalid_payload', detail: invalid }, 400);

  const requestHash = await sha256(raw);
  const contentHash = await sha256(canonical(semanticInventory(payload.inventory)));
  const receivedAt = now();
  const previous = await latest(env);
  if (previous) {
    if (payload.revision === previous.revision && (requestHash === await sha256(previous.payload_json) || contentHash === previous.content_hash)) {
      return json({ accepted: true, idempotent: true, sync: syncMeta(previous, staleAfter(env)) });
    }
    if (payload.revision <= previous.revision) return json({ error: 'out_of_order_revision', currentRevision: previous.revision }, 409);
    if (Date.parse(payload.collectedAt) < Date.parse(previous.collected_at) || Date.parse(payload.heartbeatAt) < Date.parse(previous.heartbeat_at)) {
      return json({ error: 'out_of_order_timestamp', currentRevision: previous.revision, currentCollectedAt: previous.collected_at, currentHeartbeatAt: previous.heartbeat_at }, 409);
    }
  }
  const result = await env.DB.prepare(
    'INSERT INTO guild_inventory (source, revision, collected_at, heartbeat_at, received_at, content_hash, payload_json, payload_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET revision = excluded.revision, collected_at = excluded.collected_at, heartbeat_at = excluded.heartbeat_at, received_at = excluded.received_at, content_hash = excluded.content_hash, payload_json = excluded.payload_json, payload_bytes = excluded.payload_bytes WHERE excluded.revision > guild_inventory.revision'
  ).bind(payload.source, payload.revision, payload.collectedAt, payload.heartbeatAt, receivedAt, contentHash, raw, new TextEncoder().encode(raw).byteLength).run();
  if (!result.meta?.changes) {
    const current = await latest(env);
    return json({ error: 'out_of_order_revision', currentRevision: current?.revision ?? null }, 409);
  }
  const stored = await latest(env);
  return json({ accepted: true, idempotent: false, sync: syncMeta(stored, staleAfter(env)) }, 201);
}

async function postHeartbeat(request, env) {
  if (!ingestAuthorized(request, env)) return json({ error: 'unauthorized' }, 401);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 80_000) return json({ error: 'payload_too_large' }, 413);
  let payload;
  try { payload = JSON.parse(raw); } catch { return json({ error: 'invalid_json' }, 400); }
  const invalid = validateHeartbeat(payload);
  if (invalid) return json({ error: 'invalid_payload', detail: invalid }, 400);
  const row = await latest(env);
  if (!row || row.revision !== payload.revision || row.content_hash !== payload.contentHash) return json({ error: 'out_of_order_revision', currentRevision: row?.revision ?? null, currentContentHash: row?.content_hash ?? null }, 409);
  if (Date.parse(payload.heartbeatAt) < Date.parse(row.heartbeat_at) || Date.parse(payload.collectedAt) < Date.parse(row.collected_at)) return json({ error: 'out_of_order_timestamp' }, 409);
  let stored;
  try { stored = JSON.parse(row.payload_json); } catch { return json({ error: 'stored_inventory_invalid' }, 500); }
  const merged = { ...stored, collectedAt: payload.collectedAt, heartbeatAt: payload.heartbeatAt, inventory: { ...stored.inventory, collectedAt: payload.collectedAt, sources: payload.sources, machines: payload.machines } };
  const mergedHash = await sha256(canonical(semanticInventory(merged.inventory)));
  if (mergedHash !== row.content_hash) return json({ error: 'content_changed_requires_inventory', currentRevision: row.revision }, 409);
  const receivedAt = now();
  const result = await updateHeartbeat(env, row, merged, payload.heartbeatAt, receivedAt);
  if (!result.meta?.changes) {
    const current = await latest(env);
    if (current && current.revision === payload.revision && current.content_hash === payload.contentHash && Date.parse(current.heartbeat_at) >= Date.parse(payload.heartbeatAt)) return json({ accepted: true, idempotent: true, sync: syncMeta(current, staleAfter(env)) });
    return json({ error: 'out_of_order_revision', currentRevision: current?.revision ?? null }, 409);
  }
  return json({ accepted: true, idempotent: false, sync: syncMeta(await latest(env), staleAfter(env)) }, 200);
}

async function getMeta(request, env) {
  if (!ownerAuthorized(request)) return json({ error: 'forbidden' }, 403);
  const row = await latest(env);
  if (!row) return json({ error: 'inventory_unavailable', sync: { stale: true } }, 404);
  try { return json(freshnessMeta(row, staleAfter(env))); }
  catch { return json({ error: 'stored_inventory_invalid' }, 500); }
}

async function getInventory(request, env) {
  if (!ownerAuthorized(request)) return json({ error: 'forbidden' }, 403);
  const row = await latest(env);
  if (!row) return json({ error: 'inventory_unavailable', sync: { stale: true } }, 404);
  const sync = syncMeta(row, staleAfter(env));
  const etag = `"${sync.contentHash}"`;
  if (request.headers.get('If-None-Match') === etag) return new Response(null, { status: 304, headers: { ETag: etag, 'Cache-Control': 'private, no-store' } });
  let inventory;
  try { inventory = JSON.parse(row.payload_json).inventory; } catch { return json({ error: 'stored_inventory_invalid' }, 500); }
  return json({ schemaVersion: 1, inventory, sync }, 200, { ETag: etag });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api/inventory' && request.method === 'POST') return postInventory(request, env);
    if (url.pathname === '/api/inventory/heartbeat' && request.method === 'POST') return postHeartbeat(request, env);
    if (url.pathname === '/api/inventory' && request.method === 'GET') return getInventory(request, env);
    if (url.pathname === '/api/inventory/meta' && request.method === 'GET') return getMeta(request, env);
    if (url.pathname.startsWith('/api/')) return json({ error: 'not_found' }, 404);
    if (env.ASSETS && typeof env.ASSETS.fetch === 'function') return env.ASSETS.fetch(request);
    return new Response('Not found', { status: 404 });
  },
};

export { validateEnvelope, validateItem, validateHeartbeat };
