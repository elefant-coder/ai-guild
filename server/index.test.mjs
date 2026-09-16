import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import worker, { OWNER_HEADER } from './index.js';

class MemoryD1 {
  row = null;
  prepare(sql) {
    return { bind: (...args) => ({
      first: async () => this.row,
      run: async () => {
        if (sql.startsWith('UPDATE')) {
          const [collectedAt, heartbeatAt, receivedAt, payloadJson, payloadBytes, source, revision, contentHash] = args;
          if (!this.row || source !== this.row.source || revision !== this.row.revision || contentHash !== this.row.content_hash || heartbeatAt < this.row.heartbeat_at) return { meta: { changes: 0 } };
          Object.assign(this.row, { collected_at: collectedAt, heartbeat_at: heartbeatAt, received_at: receivedAt, payload_json: payloadJson, payload_bytes: payloadBytes });
          return { meta: { changes: 1 } };
        }
        const [source, revision, collectedAt, heartbeatAt, receivedAt, contentHash, payloadJson, payloadBytes] = args;
        if (this.row && revision <= this.row.revision) return { meta: { changes: 0 } };
        this.row = { source, revision, collected_at: collectedAt, heartbeat_at: heartbeatAt, received_at: receivedAt, content_hash: contentHash, payload_json: payloadJson, payload_bytes: payloadBytes };
        return { meta: { changes: 1 } };
      },
    }) };
  }
}

const env = { DB: new MemoryD1(), GUILD_INGEST_SECRET: 'a-collector-secret-longer-than-24', GUILD_STALE_AFTER_SECONDS: '900' };
const iso = (offset = 0) => new Date(Date.now() + offset).toISOString();
const item = () => ({ id: 'cli-codex', name: 'Codex', category: 'cli', summary: 'Coding agent CLI', tags: ['agent'], status: 'observed', statusLabel: 'observed', machines: ['main'], runtimes: ['codex'], evidence: [{ source: '~/.codex/version.json', detail: 'checked', checkedAt: iso(-1_000) }] });
const envelope = (revision = 1) => {
  const collectedAt = iso(-2_000); const heartbeatAt = iso(-1_000);
  return { schemaVersion: 1, source: 'collector', revision, collectedAt, heartbeatAt, inventory: { collectedAt, sources: [{ name: 'runtime', collectedAt, count: 1, scope: 'safe' }], machines: { main: { id: 'main', status: 'observed', statusLabel: 'ok', checkedAt: collectedAt } }, notes: ['safe'], items: [item()] } };
};
const post = (body, secret = env.GUILD_INGEST_SECRET) => new Request('https://guild.test/api/inventory', { method: 'POST', headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const heartbeat = (payload, changes = {}) => new Request('https://guild.test/api/inventory/heartbeat', { method: 'POST', headers: { Authorization: `Bearer ${env.GUILD_INGEST_SECRET}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ schemaVersion: 1, source: 'collector', revision: payload.revision, contentHash: env.DB.row.content_hash, collectedAt: iso(-500), heartbeatAt: iso(), sources: [{ name: 'runtime', collectedAt: iso(-500), count: 1, scope: 'safe' }], machines: { main: { id: 'main', status: 'observed', statusLabel: 'ok', checkedAt: iso(-500) } }, ...changes }) });
const ownerGet = (path, headers = {}) => new Request(`https://guild.test${path}`, { headers: { [OWNER_HEADER]: 'owner', ...headers } });

test('inventory API: auth, strict schema, heartbeat, revisions, metadata and ETag', async () => {
  assert.equal((await worker.fetch(post(envelope(), 'wrong'), env)).status, 401, 'rejects an invalid collector secret');
  const first = envelope();
  assert.equal((await worker.fetch(post(first), env)).status, 201, 'stores valid sanitized inventory');
  assert.equal((await worker.fetch(post(first), env)).status, 200, 'accepts exact retry idempotently');
  const stale = structuredClone(first); stale.heartbeatAt = iso();
  assert.equal((await worker.fetch(post(stale), env)).status, 200, 'accepts same semantic revision after a timestamp-only retry');
  const wrongSource = envelope(2); wrongSource.source = 'laptop';
  assert.equal((await worker.fetch(post(wrongSource), env)).status, 400, 'source must be the literal collector');
  const badMachine = envelope(2); badMachine.inventory.items[0].machines = ['Mac Mini'];
  assert.equal((await worker.fetch(post(badMachine), env)).status, 400, 'machine ids are lowercase slugs');
  const password = envelope(2); password.inventory.items[0].meta = { password: 'ordinary-text-not-a-token' };
  assert.equal((await worker.fetch(post(password), env)).status, 400, 'rejects forbidden meta keys even without secret-looking values');
  const args = envelope(2); args.inventory.items[0].meta = { label: 'safe', args: ['full', 'command'] };
  assert.equal((await worker.fetch(post(args), env)).status, 400, 'rejects full command arguments');
  const token = envelope(2); token.inventory.items[0].meta = { token: 'ordinary-text-not-a-secret-pattern' };
  assert.equal((await worker.fetch(post(token), env)).status, 400, 'rejects generic token fields');
  const homePath = envelope(2); homePath.inventory.items[0].evidence[0].source = '/home/someone/.codex';
  assert.equal((await worker.fetch(post(homePath), env)).status, 400, 'rejects absolute home paths');
  const malformed = envelope(2); delete malformed.inventory.items[0].evidence;
  assert.equal((await worker.fetch(post(malformed), env)).status, 400, 'requires complete item evidence');
  const noteAbuse = envelope(2); noteAbuse.inventory.notes = [{ payload: 'not text' }];
  assert.equal((await worker.fetch(post(noteAbuse), env)).status, 400, 'rejects structured notes');
  const future = envelope(2); future.heartbeatAt = iso(6 * 60_000);
  assert.equal((await worker.fetch(post(future), env)).status, 400, 'rejects future heartbeat');
  assert.equal((await worker.fetch(new Request('https://guild.test/api/inventory'), env)).status, 403, 'does not expose data without the internal owner header');
  assert.equal((await worker.fetch(ownerGet('/api/inventory', { [OWNER_HEADER]: 'viewer' }), env)).status, 403, 'rejects a wrong owner marker');
  const meta = await worker.fetch(ownerGet('/api/inventory/meta'), env);
  assert.equal(meta.status, 200, 'returns owner-only metadata');
  const metaBody = await meta.json();
  assert.equal(metaBody.inventory.sources.length, 1, 'metadata includes source freshness');
  assert.ok(metaBody.sync.contentHash, 'metadata includes semantic content hash');
  assert.equal((await worker.fetch(heartbeat(first), env)).status, 200, 'accepts small metadata-only heartbeat');
  assert.equal((await worker.fetch(heartbeat(first), env)).status, 200, 'accepts heartbeat retry without rollback');
  assert.equal((await worker.fetch(heartbeat(first, { machines: { main: { id: 'main', status: 'observed', statusLabel: '/Users/private', checkedAt: iso() } } }), env)).status, 400, 'rejects unsafe heartbeat metadata');
  const olderHigherRevision = structuredClone(first); olderHigherRevision.revision = 2;
  const timestampConflict = await worker.fetch(post(olderHigherRevision), env);
  assert.equal(timestampConflict.status, 409, 'rejects a higher revision that would roll timestamps back');
  assert.equal(timestampConflict.headers.get('X-Current-Revision'), '1', 'exposes current revision for safe collector recovery');
  const changedHeartbeat = await worker.fetch(heartbeat(first, { machines: { main: { id: 'main', status: 'attention', statusLabel: 'changed', checkedAt: iso() } } }), env);
  assert.equal(changedHeartbeat.status, 409, 'requires full inventory when semantic data changes');
  const inventory = await worker.fetch(ownerGet('/api/inventory'), env);
  const etag = inventory.headers.get('ETag');
  assert.equal(inventory.status, 200, 'returns owner inventory');
  assert.equal((await worker.fetch(ownerGet('/api/inventory', { 'If-None-Match': etag }), env)).status, 304, 'honors ETag without retransmitting inventory');
});

test('the demo inventory passes the production schema', async () => {
  const demo = JSON.parse(await readFile(new URL('../public/demo/inventory.json', import.meta.url), 'utf8'));
  const realEnvelope = { schemaVersion: 1, source: 'collector', revision: 1, collectedAt: demo.collectedAt, heartbeatAt: iso(), inventory: demo };
  const realEnv = { ...env, DB: new MemoryD1() };
  const response = await worker.fetch(post(realEnvelope), realEnv);
  assert.equal(response.status, 201, `demo inventory accepted: ${await response.text()}`);
  const offline = structuredClone(realEnvelope);
  const [firstMachine] = Object.keys(offline.inventory.machines);
  offline.inventory.machines[firstMachine] = { ...offline.inventory.machines[firstMachine], reachability: 'unknown', stale: true, lastAttemptAt: iso(-1_000), lastSuccessfulAt: iso(-2_000) };
  assert.equal((await worker.fetch(post(offline), { ...env, DB: new MemoryD1() })).status, 201, 'retains permitted offline machine freshness metadata');
});
