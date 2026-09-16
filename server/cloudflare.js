// Outer Worker: passphrase login, signed session cookie, security headers, and routing into the Durable Object.
import { loginHtml } from './login-page.js';
import app, { OWNER_HEADER } from './index.js';
import { handleCharacterApi } from './character-api.js';
import { handleStatsApi } from './stats-api.js';

const SESSION_MS = 30 * 86400000;
const MAX_LOGIN_BYTES = 4096;
const COOKIE = '__Host-guild-session';
const encoder = new TextEncoder();
const securityHeaders = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
  // Keep same-origin form Origin intact; no-referrer can turn it into null.
  'Referrer-Policy': 'same-origin',
  'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
};
const encode = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
const decode = text => Uint8Array.from(atob(text.replaceAll('-', '+').replaceAll('_', '/')), char => char.charCodeAt(0));
const key = secret => crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);

async function session(env, host) {
  const payload = encode(encoder.encode(JSON.stringify({ exp: Date.now() + SESSION_MS, host })));
  return `${payload}.${encode(await crypto.subtle.sign('HMAC', await key(env.GUILD_SESSION_SECRET), encoder.encode(payload)))}`;
}

async function authenticated(request, env) {
  try {
    const cookies = (request.headers.get('Cookie') || '').split(';').map(value => value.trim());
    const values = cookies.filter(value => value.startsWith(`${COOKIE}=`));
    if (values.length !== 1) return false;
    const parts = values[0].slice(COOKIE.length + 1).split('.');
    if (parts.length !== 2 || parts.some(value => !/^[A-Za-z0-9_-]+$/.test(value)) || parts[0].length > 1024) return false;
    const [payload, signature] = parts;
    if (!(await crypto.subtle.verify('HMAC', await key(env.GUILD_SESSION_SECRET), decode(signature), encoder.encode(payload)))) return false;
    const value = JSON.parse(new TextDecoder().decode(decode(payload)));
    return Number.isSafeInteger(value.exp) && value.exp > Date.now() && value.exp <= Date.now() + SESSION_MS
      && value.host === new URL(request.url).host;
  } catch { return false; }
}

function protect(response) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(securityHeaders)) headers.set(name, value);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
function respond(body, status = 200, headers = {}) {
  return new Response(body, { status, headers: { ...securityHeaders, ...headers } });
}
/** Drop any caller-supplied x-guild-* header; only this wrapper may assert ownership. */
function cleaned(request, owner = false) {
  const headers = new Headers(request.headers);
  for (const name of [...headers.keys()]) if (name.startsWith('x-guild-')) headers.delete(name);
  if (owner) headers.set(OWNER_HEADER, 'owner');
  return new Request(request, { headers });
}
async function dispatch(request, env, owner = false) {
  const safe = cleaned(request, owner);
  if (env.STORE) return env.STORE.getByName('primary').fetch(safe);
  // Local tests run without a Durable Object binding and pass an in-memory DB adapter.
  const path = new URL(safe.url).pathname;
  if (path === '/api/stats') return handleStatsApi(safe, env, env.DB);
  return path.startsWith('/api/characters') ? handleCharacterApi(safe, env, env.DB) : app.fetch(safe, env);
}

async function readLogin(request) {
  if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/x-www-form-urlencoded')) return { status: 415 };
  const declared = request.headers.get('Content-Length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_LOGIN_BYTES)) return { status: 413 };
  if (!request.body) return { status: 400 };
  const reader = request.body.getReader();
  let length = 0;
  const chunks = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_LOGIN_BYTES) { await reader.cancel(); return { status: 413 }; }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const values = new URLSearchParams(new TextDecoder().decode(bytes)).getAll('password');
  return values.length === 1 ? { password: values[0] } : { status: 400 };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (typeof env.GUILD_VIEW_PASSWORD !== 'string' || env.GUILD_VIEW_PASSWORD.length < 8
      || typeof env.GUILD_SESSION_SECRET !== 'string' || env.GUILD_SESSION_SECRET.length < 16) return respond('This guild is not configured yet. Set GUILD_VIEW_PASSWORD and GUILD_SESSION_SECRET (see docs/DEPLOY.md).', 503);
    if (url.pathname === '/login') {
      if (request.method === 'GET') return respond(loginHtml(), 200, { 'Content-Type': 'text/html; charset=utf-8' });
      if (request.method !== 'POST') return respond('Method not allowed', 405, { Allow: 'GET, POST' });
      if (request.headers.get('Origin') !== url.origin) return respond('Forbidden', 403);
      const value = await readLogin(request);
      if (value.status) return respond('Invalid login request', value.status);
      const passwordKey = await key(env.GUILD_SESSION_SECRET);
      const expected = await crypto.subtle.sign('HMAC', passwordKey, encoder.encode(env.GUILD_VIEW_PASSWORD));
      if (!(await crypto.subtle.verify('HMAC', passwordKey, expected, encoder.encode(value.password)))) return respond(loginHtml(true), 401, { 'Content-Type': 'text/html; charset=utf-8' });
      return respond(null, 303, { Location: '/', 'Set-Cookie': `${COOKIE}=${await session(env, url.host)}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=2592000` });
    }
    if (url.pathname === '/logout') {
      if (request.method !== 'POST') return respond('Method not allowed', 405, { Allow: 'POST' });
      if (request.headers.get('Origin') !== url.origin) return respond('Forbidden', 403);
      return respond(null, 303, { Location: '/login', 'Set-Cookie': `${COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0` });
    }
    const api = url.pathname.startsWith('/api/');
    const ingest = ['/api/inventory', '/api/inventory/heartbeat', '/api/characters', '/api/stats'].includes(url.pathname)
      || /^\/api\/characters\/[A-Za-z0-9@-]{1,160}\/image$/.test(url.pathname);
    if (request.method === 'POST' && ingest) return protect(await dispatch(request, env));
    if (!(await authenticated(request, env))) return api
      ? respond(JSON.stringify({ error: 'unauthorized' }), 401, { 'Content-Type': 'application/json' })
      : respond(null, 302, { Location: '/login' });
    if (api) return protect(await dispatch(request, env, true));
    return protect(await app.fetch(cleaned(request, true), env));
  },
};
