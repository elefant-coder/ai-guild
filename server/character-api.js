// Character art registry: the collector registers descriptors, uploads images, and the browser reads a manifest.
import { SOURCE, ingestAuthorized, ownerAuthorized } from './index.js';

const MAX_JSON_BYTES = 128 * 1024;
const MAX_IMAGE_BYTES = 512 * 1024;
const MAX_BATCH = 500;
const ID = /^[A-Za-z0-9@-]{1,160}$/;
const HASH = /^[a-f0-9]{64}$/;
const CATEGORIES = ['agent', 'skill', 'harness', 'automation'];

export const CHARACTER_SCHEMA = `
  CREATE TABLE IF NOT EXISTS guild_characters (
    id TEXT PRIMARY KEY NOT NULL,
    category TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    image_hash TEXT,
    image BLOB,
    mime TEXT,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL
  )
`;

const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'private, no-store', ...headers },
});
const now = () => new Date().toISOString();
const safeText = (value, max) => typeof value === 'string' && value.length > 0 && value.length <= max;

function secureEqual(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string' || left.length !== right.length) return false;
  let result = 0;
  for (let i = 0; i < left.length; i += 1) result |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return result === 0;
}

async function boundedBytes(request, maximum) {
  const declared = request.headers.get('Content-Length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) throw new RangeError('too_large');
  if (!request.body) throw new TypeError('missing_body');
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) { await reader.cancel(); throw new RangeError('too_large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength; }
  return output;
}
async function boundedJson(request) {
  const bytes = await boundedBytes(request, MAX_JSON_BYTES);
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new SyntaxError('invalid_json'); }
}
async function sha256(bytes) {
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function imageMime(request, bytes) {
  const mime = (request.headers.get('Content-Type') || '').split(';', 1)[0].trim().toLowerCase();
  const png = bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47 && bytes[4] === 0x0d && bytes[5] === 0x0a && bytes[6] === 0x1a && bytes[7] === 0x0a;
  const webp = bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP';
  if (mime === 'image/png' && png) return mime;
  if (mime === 'image/webp' && webp) return mime;
  return null;
}
function characterId(pathname) {
  const match = pathname.match(/^\/api\/characters\/([A-Za-z0-9@-]{1,160})\/image$/);
  return match?.[1] || null;
}
function statusCounts(rows) {
  return rows.reduce((counts, row) => {
    if (['ready', 'queued', 'generating', 'failed'].includes(row.status)) counts[row.status] += 1;
    return counts;
  }, { ready: 0, queued: 0, generating: 0, failed: 0 });
}
async function currentInventoryIds(db) {
  const row = await db.prepare('SELECT payload_json FROM guild_inventory WHERE source = ?').bind(SOURCE).first();
  if (!row || typeof row.payload_json !== 'string') return null;
  try {
    const items = JSON.parse(row.payload_json)?.inventory?.items;
    if (!Array.isArray(items)) return null;
    return new Map(items.filter(item => item && CATEGORIES.includes(item.category) && ID.test(item.id)).map(item => [item.id, item.category]));
  } catch { return null; }
}

async function manifest(request, env, db) {
  if (!ownerAuthorized(request)) return json({ error: 'forbidden' }, 403);
  const rows = await db.prepare('SELECT id, status, updated_at, prompt_version FROM guild_characters ORDER BY id').bind().all();
  const characters = {};
  for (const row of rows) {
    if (!ID.test(row.id) || !['ready', 'queued', 'generating', 'failed'].includes(row.status) || !safeText(row.updated_at, 64) || !safeText(row.prompt_version, 120)) continue;
    characters[row.id] = { status: row.status, imageUrl: `/api/characters/${row.id}/image`, updatedAt: row.updated_at, promptVersion: row.prompt_version };
  }
  return json({ schemaVersion: 1, characters, counts: statusCounts(Object.values(characters)), updatedAt: now() });
}

async function register(request, env, db) {
  if (!ingestAuthorized(request, env)) return json({ error: 'unauthorized' }, 401);
  let payload;
  try { payload = await boundedJson(request); }
  catch (error) { return json({ error: error instanceof RangeError ? 'payload_too_large' : 'invalid_json' }, error instanceof RangeError ? 413 : 400); }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || Object.keys(payload).some(key => !['schemaVersion', 'promptVersion', 'characters'].includes(key))
    || payload.schemaVersion !== 1 || !safeText(payload.promptVersion, 120) || !Array.isArray(payload.characters) || payload.characters.length > MAX_BATCH) return json({ error: 'invalid_payload' }, 400);
  const inventoryIds = await currentInventoryIds(db);
  if (!inventoryIds) return json({ error: 'inventory_unavailable' }, 409);
  const unique = new Set();
  for (const character of payload.characters) {
    if (!character || typeof character !== 'object' || Array.isArray(character) || Object.keys(character).some(key => !['id', 'category', 'contentHash'].includes(key))
      || !ID.test(character.id) || !CATEGORIES.includes(character.category) || !HASH.test(character.contentHash)
      || unique.has(character.id) || inventoryIds.get(character.id) !== character.category) return json({ error: 'invalid_payload' }, 400);
    unique.add(character.id);
  }
  let queued = 0; let unchanged = 0;
  for (const character of payload.characters) {
    const previous = await db.prepare('SELECT category, content_hash, prompt_version, status FROM guild_characters WHERE id = ?').bind(character.id).first();
    const changed = !previous || previous.category !== character.category || previous.content_hash !== character.contentHash || previous.prompt_version !== payload.promptVersion;
    if (changed) {
      await db.prepare('INSERT INTO guild_characters (id, category, content_hash, image_hash, image, mime, status, updated_at, prompt_version) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET category = excluded.category, content_hash = excluded.content_hash, image_hash = NULL, image = NULL, mime = NULL, status = excluded.status, updated_at = excluded.updated_at, prompt_version = excluded.prompt_version')
        .bind(character.id, character.category, character.contentHash, 'queued', now(), payload.promptVersion).run();
      queued += 1;
    } else unchanged += 1;
  }
  return json({ accepted: true, queued, unchanged }, 202);
}

async function image(request, env, db, id) {
  if (request.method === 'GET') {
    if (!ownerAuthorized(request)) return json({ error: 'forbidden' }, 403);
    const row = await db.prepare('SELECT image, mime, image_hash, status FROM guild_characters WHERE id = ?').bind(id).first();
    if (!row || row.status !== 'ready' || !row.image || !['image/webp', 'image/png'].includes(row.mime) || !HASH.test(row.image_hash)) return json({ error: 'not_found' }, 404);
    const etag = `"${row.image_hash}"`;
    if (request.headers.get('If-None-Match') === etag) return new Response(null, { status: 304, headers: { ETag: etag, 'Cache-Control': 'private, no-store' } });
    return new Response(row.image, { status: 200, headers: { 'Content-Type': row.mime, ETag: etag, 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' } });
  }
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405, { Allow: 'GET, POST' });
  if (!ingestAuthorized(request, env)) return json({ error: 'unauthorized' }, 401);
  const existing = await db.prepare('SELECT category, content_hash, status, image_hash FROM guild_characters WHERE id = ?').bind(id).first();
  if (!existing) return json({ error: 'not_found' }, 404);
  const descriptor = request.headers.get('X-Character-Descriptor') || '';
  if (!HASH.test(descriptor) || !HASH.test(existing.content_hash) || !secureEqual(descriptor, existing.content_hash)) return json({ error: 'descriptor_mismatch' }, 409);
  const inventoryIds = await currentInventoryIds(db);
  if (!inventoryIds || inventoryIds.get(id) !== existing.category) return json({ error: 'not_current' }, 409);
  let bytes;
  try { bytes = await boundedBytes(request, MAX_IMAGE_BYTES); }
  catch (error) { return json({ error: error instanceof RangeError ? 'payload_too_large' : 'invalid_image' }, error instanceof RangeError ? 413 : 400); }
  const mime = imageMime(request, bytes);
  if (!mime) return json({ error: 'invalid_image' }, 400);
  const imageHash = await sha256(bytes);
  if (existing.status === 'ready') {
    if (secureEqual(existing.image_hash || '', imageHash)) return json({ accepted: true, id, status: 'ready', idempotent: true, imageHash }, 200);
    // Re-uploading different art for the same descriptor is allowed: the collector is the single writer.
  }
  const result = await db.prepare('UPDATE guild_characters SET image = ?, mime = ?, image_hash = ?, status = ?, updated_at = ? WHERE id = ? AND content_hash = ?')
    .bind(bytes, mime, imageHash, 'ready', now(), id, descriptor).run();
  if (!result.meta?.changes) return json({ error: 'descriptor_mismatch' }, 409);
  return json({ accepted: true, id, status: 'ready', imageHash }, 201);
}

export async function handleCharacterApi(request, env, db) {
  const url = new URL(request.url);
  if (url.pathname === '/api/characters') {
    if (request.method === 'GET') return manifest(request, env, db);
    if (request.method === 'POST') return register(request, env, db);
    return json({ error: 'method_not_allowed' }, 405, { Allow: 'GET, POST' });
  }
  const id = characterId(url.pathname);
  return id ? image(request, env, db, id) : json({ error: 'not_found' }, 404);
}
