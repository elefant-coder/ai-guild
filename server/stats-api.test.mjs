import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { STATS_SCHEMA, handleStatsApi, validStats } from './stats-api.js';
import { OWNER_HEADER } from './index.js';
const env = { GUILD_INGEST_SECRET: 'x'.repeat(30) };
const headers = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + env.GUILD_INGEST_SECRET };
const zero = () => ({ input: 0, output: 0, cachedInput: 0, total: 0 });
function fixture() {
  return { schemaVersion: 1, collectedAt: '2026-01-07T12:00:00Z', scope: 'This machine', timezone: 'UTC', totals: { input: 200, output: 50, cachedInput: 120, total: 250 }, today: zero(), days: Array.from({ length: 7 }, (_, i) => ({ date: `2026-01-0${i + 1}`, ...zero() })), providers: [{ id: 'codex', name: 'Codex', totals: { input: 200, output: 50, cachedInput: 120, total: 250 }, today: zero(), sessions: 2 }, { id: 'claude', name: 'Claude Code', totals: zero(), today: zero(), sessions: 0 }], coverage: { startedAt: '2026-01-01T00:00:00Z', notes: ['This machine only'] } };
}
function database() { const sql = new DatabaseSync(':memory:'); sql.exec(STATS_SCHEMA); return { sql, prepare: statement => ({ bind: (...args) => ({ first: async () => sql.prepare(statement).get(...args), run: async () => ({ meta: { changes: Number(sql.prepare(statement).run(...args).changes) } }) }) }) }; }
const post = v => new Request('https://guild.test/api/stats', { method: 'POST', headers, body: JSON.stringify(v) });
const get = () => new Request('https://guild.test/api/stats', { headers: { [OWNER_HEADER]: 'owner' } });

test('stats validates provider sums, included cache, timezone-bucketed consecutive days and provider count', () => {
  assert.equal(validStats(fixture()), true);
  const single = fixture(); single.providers = [single.providers[0]]; assert.equal(validStats(single), true, 'one provider is fine');
  const tokyo = fixture(); tokyo.timezone = 'Asia/Tokyo'; tokyo.collectedAt = '2026-01-07T20:00:00Z'; tokyo.days = Array.from({ length: 7 }, (_, i) => ({ date: `2026-01-0${i + 2}`, ...zero() })); assert.equal(validStats(tokyo), true, 'days end on today in the stated timezone');
  for (const change of [v => v.totals.total += 120, v => v.providers[0].today.total = 1, v => v.days[1].date = '2026-01-01', v => v.coverage.token = 'secret', v => v.days[0].cachedInput = 5, v => v.scope = '', v => v.today.input = true, v => v.timezone = 'Not/AZone', v => v.providers = [], v => delete v.timezone]) {
    const v = fixture(); change(v); assert.equal(validStats(v), false);
  }
});
test('private statistics cannot be read without the owner marker or written with a viewing cookie', async t => {
  const db = database(); t.after(() => db.sql.close());
  assert.equal((await handleStatsApi(new Request('https://guild.test/api/stats'), env, db)).status, 403);
  assert.equal((await handleStatsApi(new Request('https://guild.test/api/stats', { method: 'POST', headers: { Cookie: 'fake' }, body: '{}' }), env, db)).status, 401);
  assert.equal((await handleStatsApi(get(), env, db)).status, 404);
});
test('real SQLite stats retries are idempotent and older snapshots cannot roll back counters', async t => {
  const db = database(); t.after(() => db.sql.close()); const v = fixture();
  assert.equal((await handleStatsApi(post(v), env, db)).status, 202);
  assert.equal((await handleStatsApi(post(v), env, db)).status, 200);
  const old = fixture(); old.collectedAt = '2026-01-07T11:00:00Z'; assert.equal((await handleStatsApi(post(old), env, db)).status, 409);
  const response = await handleStatsApi(get(), env, db); assert.equal(response.headers.get('Cache-Control'), 'private, no-store'); assert.deepEqual(await response.json(), v);
});
test('oversized or extra fields never enter the statistics store', async t => {
  const db = database(); t.after(() => db.sql.close());
  assert.equal((await handleStatsApi(new Request('https://guild.test/api/stats', { method: 'POST', headers, body: ' '.repeat(32769) }), env, db)).status, 413);
  const v = fixture(); v.messages = ['private']; assert.equal((await handleStatsApi(post(v), env, db)).status, 400);
});
