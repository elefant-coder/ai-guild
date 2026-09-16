import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { CHARACTER_SCHEMA, handleCharacterApi } from './character-api.js';
import { OWNER_HEADER } from './index.js';

class SqlDb {
  constructor() { this.sqlite = new DatabaseSync(':memory:'); }
  prepare(sql) {
    return { bind: (...values) => ({
      first: async () => this.sqlite.prepare(sql).get(...values) ?? null,
      all: async () => this.sqlite.prepare(sql).all(...values),
      run: async () => ({ meta: { changes: Number(this.sqlite.prepare(sql).run(...values).changes) } }),
    }) };
  }
  close() { this.sqlite.close(); }
}

const env = { GUILD_INGEST_SECRET: 'x'.repeat(30) };
const descriptor = first => `${first.slice(0, 1)}${'a'.repeat(63)}`;
const registry = (characters, promptVersion = 'lavender-toybox-v1') => new Request('https://guild.test/api/characters', {
  method: 'POST', headers: { Authorization: `Bearer ${env.GUILD_INGEST_SECRET}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ schemaVersion: 1, promptVersion, characters }),
});
const ownerRequest = (path, headers = {}) => new Request(`https://guild.test${path}`, { headers: { [OWNER_HEADER]: 'owner', ...headers } });
const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const imageHeaders = (hash, mime = 'image/png') => ({ Authorization: `Bearer ${env.GUILD_INGEST_SECRET}`, 'Content-Type': mime, 'X-Character-Descriptor': hash });

function dbWithInventory() {
  const db = new SqlDb();
  db.sqlite.exec(CHARACTER_SCHEMA);
  db.sqlite.exec('CREATE TABLE guild_inventory (source TEXT PRIMARY KEY, payload_json TEXT NOT NULL)');
  const inventory = { inventory: { items: [
    { id: 'agent-compass', category: 'agent' }, { id: 'skill-video-cut', category: 'skill' }, { id: 'cli-codex', category: 'cli' },
  ] } };
  db.sqlite.prepare('INSERT INTO guild_inventory (source, payload_json) VALUES (?, ?)').run('collector', JSON.stringify(inventory));
  return db;
}

test('registry accepts only current agent/skill IDs and exposes a bounded owner manifest', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  assert.equal((await handleCharacterApi(new Request('https://guild.test/api/characters'), env, db)).status, 403);
  assert.equal((await handleCharacterApi(new Request('https://guild.test/api/characters', { method: 'POST', body: '{}' }), env, db)).status, 401);
  const accepted = await handleCharacterApi(registry([{ id: 'agent-compass', category: 'agent', contentHash: descriptor('a') }, { id: 'skill-video-cut', category: 'skill', contentHash: descriptor('b') }]), env, db);
  assert.equal(accepted.status, 202); assert.deepEqual(await accepted.json(), { accepted: true, queued: 2, unchanged: 0 });
  const invalid = await handleCharacterApi(registry([{ id: 'cli-codex', category: 'agent', contentHash: descriptor('c') }]), env, db);
  assert.equal(invalid.status, 400);
  const manifest = await handleCharacterApi(ownerRequest('/api/characters'), env, db);
  const body = await manifest.json();
  assert.equal(body.schemaVersion, 1); assert.equal(body.counts.queued, 2); assert.equal(body.characters['agent-compass'].status, 'queued');
  assert.equal(body.characters['agent-compass'].imageUrl, '/api/characters/agent-compass/image');
});

test('image upload validates magic bytes, supports exact retries and replacement, and privately serves ETags', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  const inputHash = descriptor('a');
  await handleCharacterApi(registry([{ id: 'agent-compass', category: 'agent', contentHash: inputHash }]), env, db);
  const upload = () => new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: imageHeaders(inputHash), body: png });
  const wrong = new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: imageHeaders(descriptor('b')), body: png });
  assert.equal((await handleCharacterApi(wrong, env, db)).status, 409, 'wrong descriptor is rejected before image storage');
  const first = await handleCharacterApi(upload(), env, db); assert.equal(first.status, 201); const firstBody = await first.json(); assert.match(firstBody.imageHash, /^[a-f0-9]{64}$/);
  assert.equal((await handleCharacterApi(upload(), env, db)).status, 200, 'safe upload retry is idempotent');
  const changed = new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: imageHeaders(inputHash), body: new Uint8Array([...png, 1]) });
  assert.equal((await handleCharacterApi(changed, env, db)).status, 201, 'the collector may replace art for the same descriptor');
  assert.equal((await handleCharacterApi(new Request('https://guild.test/api/characters/agent-compass/image'), env, db)).status, 403);
  const get = await handleCharacterApi(ownerRequest('/api/characters/agent-compass/image'), env, db);
  assert.equal(get.status, 200); assert.equal(get.headers.get('Content-Type'), 'image/png'); assert.equal(get.headers.get('Cache-Control'), 'private, no-store'); assert.equal((await get.arrayBuffer()).byteLength, png.byteLength + 1);
  const etag = get.headers.get('ETag');
  assert.equal((await handleCharacterApi(ownerRequest('/api/characters/agent-compass/image', { 'If-None-Match': etag }), env, db)).status, 304);
});

test('descriptor CAS prevents a stale upload from overwriting a newly queued version', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  const oldHash = descriptor('a'); const newHash = descriptor('b');
  await handleCharacterApi(registry([{ id: 'agent-compass', category: 'agent', contentHash: oldHash }]), env, db);
  db.sqlite.prepare('UPDATE guild_characters SET content_hash = ?, status = ? WHERE id = ?').run(newHash, 'queued', 'agent-compass');
  const stale = new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: imageHeaders(oldHash), body: png });
  assert.equal((await handleCharacterApi(stale, env, db)).status, 409);
  const row = await db.prepare('SELECT content_hash, image, status FROM guild_characters WHERE id = ?').bind('agent-compass').first();
  assert.equal(row.content_hash, newHash); assert.equal(row.image, null); assert.equal(row.status, 'queued');
});

test('image MIME requires matching PNG or real WEBP signatures', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  const inputHash = descriptor('b');
  await handleCharacterApi(registry([{ id: 'skill-video-cut', category: 'skill', contentHash: inputHash }]), env, db);
  const fakeWebp = new Uint8Array([...new TextEncoder().encode('RIFFxxxxNOPE')]);
  const response = await handleCharacterApi(new Request('https://guild.test/api/characters/skill-video-cut/image', { method: 'POST', headers: imageHeaders(inputHash, 'image/webp'), body: fakeWebp }), env, db);
  assert.equal(response.status, 400);
});

test('a changed descriptor invalidates old art, while identical registration preserves it', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  const character = { id: 'agent-compass', category: 'agent', contentHash: descriptor('a') };
  await handleCharacterApi(registry([character]), env, db);
  await handleCharacterApi(new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: imageHeaders(character.contentHash), body: png }), env, db);
  assert.deepEqual(await (await handleCharacterApi(registry([character]), env, db)).json(), { accepted: true, queued: 0, unchanged: 1 });
  assert.equal((await handleCharacterApi(ownerRequest('/api/characters/agent-compass/image'), env, db)).status, 200);
  await handleCharacterApi(registry([{ ...character, contentHash: descriptor('b') }]), env, db);
  assert.equal((await handleCharacterApi(ownerRequest('/api/characters/agent-compass/image'), env, db)).status, 404);
});

test('rejects oversized binary before it can replace queued art', async t => {
  const db = dbWithInventory(); t.after(() => db.close());
  const inputHash = descriptor('a');
  await handleCharacterApi(registry([{ id: 'agent-compass', category: 'agent', contentHash: inputHash }]), env, db);
  const oversized = new Uint8Array(512 * 1024 + 1); oversized.set(png);
  const response = await handleCharacterApi(new Request('https://guild.test/api/characters/agent-compass/image', { method: 'POST', headers: { ...imageHeaders(inputHash), 'Content-Length': String(oversized.byteLength) }, body: oversized }), env, db);
  assert.equal(response.status, 413);
  const row = await db.prepare('SELECT status, image FROM guild_characters WHERE id = ?').bind('agent-compass').first();
  assert.equal(row.status, 'queued'); assert.equal(row.image, null);
});
