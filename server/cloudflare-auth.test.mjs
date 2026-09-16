import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import app from './cloudflare.js';
import { OWNER_HEADER } from './index.js';
const origin = 'https://guild.example';
const env = {
  GUILD_VIEW_PASSWORD: 'correct horse', GUILD_SESSION_SECRET: 'session-secret-long', GUILD_INGEST_SECRET: 'x'.repeat(30),
  DB: { prepare() { return { bind() { return { first: async () => null, all: async () => [], run: async () => ({ meta: { changes: 1 } }) }; } }; } },
  ASSETS: { fetch: async () => new Response('asset', { headers: { 'Cache-Control': 'public,max-age=1' } }) },
};
const req = (path, options = {}) => new Request(origin + path, options);
const formHeaders = { Origin: origin, 'Content-Type': 'application/x-www-form-urlencoded' };
const login = (password = env.GUILD_VIEW_PASSWORD) => app.fetch(req('/login', { method: 'POST', headers: formHeaders, body: new URLSearchParams({ password }) }), env);
const cookie = async () => (await login()).headers.get('Set-Cookie').split(';')[0];
function signedCookie(value) {
  const payload = Buffer.from(JSON.stringify(value)).toString('base64url');
  return `__Host-guild-session=${payload}.${createHmac('sha256', env.GUILD_SESSION_SECRET).update(payload).digest('base64url')}`;
}

test('login page uses the guild name and correct login issues a secure session', async () => {
  const page = await app.fetch(req('/login'), env);
  assert.equal(page.status, 200); assert.match(await page.text(), /AI Guild/);
  const wrong = await login('wrong');
  assert.equal(wrong.status, 401); assert.match(await wrong.text(), /role="alert"/);
  const success = await login(); assert.equal(success.status, 303);
  assert.equal(success.headers.get('Referrer-Policy'), 'same-origin');
  assert.equal(success.headers.get('Location'), '/');
  assert.match(success.headers.get('Set-Cookie'), /__Host-guild-session=.*HttpOnly; Secure; SameSite=Strict; Path=\/; Max-Age=2592000/);
});
test('all assets and API reads deny anonymous and spoofed owner headers', async () => {
  for (const path of ['/', '/assets/index.js', '/art/navigation/guild.webp', '/api/inventory', '/api/inventory/meta', '/api/characters', '/api/characters/agent-atlas/image', '/api/stats']) {
    const response = await app.fetch(req(path, { headers: { [OWNER_HEADER]: 'owner' } }), env);
    assert.equal(response.status, path.startsWith('/api/') ? 401 : 302, path);
    assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
  }
});
test('login requires same Origin, correct method, urlencoded bounded request', async () => {
  for (const Origin of [undefined, 'https://evil.example']) {
    const headers = { 'Content-Type': formHeaders['Content-Type'], ...(Origin ? { Origin } : {}) };
    assert.equal((await app.fetch(req('/login', { method: 'POST', headers, body: 'password=hello' }), env)).status, 403);
  }
  assert.equal((await app.fetch(req('/login', { method: 'PUT' }), env)).status, 405);
  assert.equal((await app.fetch(req('/login', { method: 'POST', headers: { Origin: origin }, body: '{}' }), env)).status, 415);
  assert.equal((await app.fetch(req('/login', { method: 'POST', headers: formHeaders, body: 'x'.repeat(4097) }), env)).status, 413);
  assert.equal((await app.fetch(req('/login', { method: 'POST', headers: { ...formHeaders, 'Content-Length': '999999' }, body: 'x' }), env)).status, 413);
  assert.equal((await app.fetch(req('/login', { method: 'POST', headers: formHeaders, body: 'password=a&password=b' }), env)).status, 400);
});
test('tampered, trailing, duplicate, expired, future and foreign-host sessions fail', async () => {
  const valid = await cookie();
  const cases = [valid + 'x', valid + '.extra', valid + '; ' + valid,
    signedCookie({ exp: Date.now() - 1, host: 'guild.example' }),
    signedCookie({ exp: Date.now() + 31 * 86400000, host: 'guild.example' }),
    signedCookie({ exp: Date.now() + 60000, host: 'other.example' })];
  for (const value of cases) assert.equal((await app.fetch(req('/', { headers: { Cookie: value } }), env)).status, 302);
});
test('missing configuration fails closed even with a valid cookie', async () => {
  for (const bad of [{ GUILD_VIEW_PASSWORD: '' }, { GUILD_SESSION_SECRET: undefined }])
    assert.equal((await app.fetch(req('/', { headers: { Cookie: await cookie() } }), { ...env, ...bad })).status, 503);
});
test('authenticated assets stay out of the Durable Object and always carry private security headers', async () => {
  const response = await app.fetch(req('/assets/index.js', { headers: { Cookie: await cookie() } }), {
    ...env, STORE: { getByName() { throw new Error('assets must not reach the DO'); } },
  });
  assert.equal(response.status, 200); assert.equal(await response.text(), 'asset');
  assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
  assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
  assert.match(response.headers.get('Content-Security-Policy'), /frame-ancestors 'none'/);
});
test('authenticated API injects the owner marker only after cookie verification and strips spoofed headers', async () => {
  let received;
  const response = await app.fetch(req('/api/inventory/meta', { headers: { Cookie: await cookie(), 'x-guild-authenticated': 'owner', 'x-guild-anything': 'spoof' } }), {
    ...env, STORE: { getByName(name) { assert.equal(name, 'primary'); return { fetch(request) { received = request; return new Response('{}'); } }; } },
  });
  assert.equal(response.status, 200);
  assert.equal(received.headers.get(OWNER_HEADER), 'owner');
  assert.equal(received.headers.get('x-guild-anything'), null);
  assert.equal(response.headers.get('Cache-Control'), 'private, no-store');
  const local = await app.fetch(req('/api/inventory/meta', { headers: { Cookie: await cookie() } }), env);
  assert.equal(local.status, 404);
  assert.equal((await local.json()).error, 'inventory_unavailable'); // authorized, empty store
});
test('ingestion requires the independent bearer, even with a valid browser cookie', async () => {
  for (const path of ['/api/inventory', '/api/inventory/heartbeat', '/api/stats', '/api/characters', '/api/characters/agent-atlas/image']) {
    for (const Authorization of [undefined, 'Bearer wrong']) {
      const response = await app.fetch(req(path, { method: 'POST', headers: { Cookie: await cookie(), ...(Authorization ? { Authorization } : {}), 'Content-Type': 'application/json' }, body: '{}' }), env);
      assert.equal(response.status, 401, path);
    }
  }
  for (const path of ['/api/inventory', '/api/inventory/heartbeat']) {
    const response = await app.fetch(req(path, { method: 'POST', headers: { Authorization: 'Bearer ' + env.GUILD_INGEST_SECRET, 'Content-Type': 'application/json' }, body: '{}' }), env);
    assert.equal(response.status, 400); // passed bearer check, rejected invalid payload
  }
});
test('logout requires same-origin POST and expires the session', async () => {
  assert.equal((await app.fetch(req('/logout'), env)).status, 405);
  assert.equal((await app.fetch(req('/logout', { method: 'POST' }), env)).status, 403);
  const response = await app.fetch(req('/logout', { method: 'POST', headers: { Origin: origin, Cookie: await cookie() } }), env);
  assert.equal(response.status, 303); assert.match(response.headers.get('Set-Cookie'), /Max-Age=0/);
});
