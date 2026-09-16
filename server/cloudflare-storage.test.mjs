import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { DatabaseSync } from 'node:sqlite';

// Cloudflare SQL cursors are iterable. Every statement below runs in real SQLite.
class NodeSql {
  constructor() { this.database = new DatabaseSync(':memory:'); this.calls = []; }
  exec(statement, ...parameters) {
    this.calls.push([statement, parameters]); const query = statement.trim();
    if (/^CREATE\s+/i.test(query)) { this.database.exec(query); return []; }
    const prepared = this.database.prepare(query);
    if (/^SELECT\s+/i.test(query)) return prepared.all(...parameters);
    prepared.run(...parameters); return [];
  }
  close() { this.database.close(); }
}
async function moduleUnderTest() {
  const path = new URL('./cloudflare-storage.js', import.meta.url); let source = await readFile(path, 'utf8');
  source = source.replace("from './character-api.js'", `from '${new URL('./character-api.js', import.meta.url).href}'`);
  source = source.replace("from './stats-api.js'", `from '${new URL('./stats-api.js', import.meta.url).href}'`);
  source = source.replace("import { DurableObject } from 'cloudflare:workers';", 'class DurableObject { constructor(){} }').replace("import app from './index.js';", 'const app={fetch:async(_request,env)=>new Response(JSON.stringify({hasDb:!!env.DB}))};');
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}
const insert = `INSERT INTO guild_inventory (source, revision, collected_at, heartbeat_at, received_at, content_hash, payload_json, payload_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET revision = excluded.revision, collected_at = excluded.collected_at, heartbeat_at = excluded.heartbeat_at, received_at = excluded.received_at, content_hash = excluded.content_hash, payload_json = excluded.payload_json, payload_bytes = excluded.payload_bytes WHERE excluded.revision > guild_inventory.revision`;
const select = 'SELECT source, revision, collected_at, heartbeat_at, received_at, content_hash, payload_json FROM guild_inventory WHERE source = ?';
const heartbeat = `UPDATE guild_inventory SET collected_at = ?, heartbeat_at = ?, received_at = ?, payload_json = ?, payload_bytes = ? WHERE source = ? AND revision = ? AND content_hash = ? AND heartbeat_at <= ?`;

test('real SQLite adapter preserves bindings, null reads, and immediate write changes', async t => {
  const { GuildStore } = await moduleUnderTest(), sql = new NodeSql(); t.after(() => sql.close()); const db = new GuildStore({ storage: { sql } }, {}).db;
  assert.equal(await db.prepare(select).bind('collector').first(), null);
  assert.equal((await db.prepare(insert).bind('collector', 2, '2026-01-01T00:00:00.000Z', '2026-01-01T00:01:00.000Z', '2026-01-01T00:01:01.000Z', 'a'.repeat(64), '{}', 2).run()).meta.changes, 1);
  assert.deepEqual(await db.prepare(select).bind('collector').first(), { source: 'collector', revision: 2, collected_at: '2026-01-01T00:00:00.000Z', heartbeat_at: '2026-01-01T00:01:00.000Z', received_at: '2026-01-01T00:01:01.000Z', content_hash: 'a'.repeat(64), payload_json: '{}' });
  assert.equal((await db.prepare(insert).bind('collector', 2, 'new', 'new', 'new', 'b'.repeat(64), '{}', 2).run()).meta.changes, 0);
  assert.equal((await db.prepare(select).bind('collector').first()).content_hash, 'a'.repeat(64));
});
test('real SQLite heartbeat CAS never overwrites a stale timestamp or revision', async t => {
  const { GuildStore } = await moduleUnderTest(), sql = new NodeSql(); t.after(() => sql.close()); const db = new GuildStore({ storage: { sql } }, {}).db, hash = 'c'.repeat(64);
  await db.prepare(insert).bind('collector', 4, '2026-01-01T00:00:00.000Z', '2026-01-01T00:01:00.000Z', '2026-01-01T00:01:01.000Z', hash, '{}', 2).run();
  assert.equal((await db.prepare(heartbeat).bind('old', '2026-01-01T00:00:00.000Z', 'old', '{}', 2, 'collector', 4, hash, '2026-01-01T00:00:00.000Z').run()).meta.changes, 0);
  assert.equal((await db.prepare(heartbeat).bind('bad-revision', '2026-01-01T00:02:00.000Z', 'bad', '{}', 2, 'collector', 3, hash, '2026-01-01T00:02:00.000Z').run()).meta.changes, 0);
  assert.equal((await db.prepare(heartbeat).bind('fresh', '2026-01-01T00:02:00.000Z', 'fresh', '{}', 2, 'collector', 4, hash, '2026-01-01T00:02:00.000Z').run()).meta.changes, 1);
  const stored = await db.prepare(select).bind('collector').first(); assert.equal(stored.heartbeat_at, '2026-01-01T00:02:00.000Z'); assert.equal(stored.collected_at, 'fresh');
});
test('Durable Object creates the schema and passes its adapter to the inventory app', async t => {
  const { GuildStore } = await moduleUnderTest(), sql = new NodeSql(); t.after(() => sql.close()); const object = new GuildStore({ storage: { sql } }, { GUILD_INGEST_SECRET: 'x'.repeat(30) }), response = await object.fetch(new Request('https://guild.example/api/inventory'));
  assert.match(sql.calls[0][0], /CREATE TABLE IF NOT EXISTS guild_inventory/); assert.deepEqual(await response.json(), { hasDb: true });
});
