import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Vite-only preview of the API. Serves data/ (your real scan) when present, otherwise public/demo/ (fictional data).
// Production uses the authenticated Worker + Durable Object instead; nothing here is bundled.
export function localPreview() {
  const root = new URL('../', import.meta.url);
  const pick = (real, demo) => existsSync(new URL(real, root)) ? new URL(real, root) : new URL(demo, root);
  return {
    name: 'guild-local-preview',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const route = request.url?.split('?')[0];
        const send = (status, body) => { response.statusCode = status; response.setHeader('Content-Type', 'application/json'); response.setHeader('Cache-Control', 'no-store'); response.end(body); };
        if (request.method === 'GET' && route === '/api/stats') {
          try { send(200, await readFile(pick('data/stats.json', 'public/demo/stats.json'), 'utf8')); }
          catch { send(503, JSON.stringify({ error: 'stats_unavailable' })); }
          return;
        }
        if (request.method === 'GET' && route === '/api/characters') {
          try { send(200, await readFile(pick('public/art/characters.json', 'public/demo/characters.json'), 'utf8')); }
          catch { send(200, JSON.stringify({ schemaVersion: 1, characters: {} })); }
          return;
        }
        if (request.method !== 'GET' || !['/api/inventory', '/api/inventory/meta'].includes(route)) return next();
        try {
          const inventory = JSON.parse(await readFile(pick('data/inventory.json', 'public/demo/inventory.json'), 'utf8'));
          const contentHash = createHash('sha256').update(JSON.stringify(inventory)).digest('hex');
          const nowIso = new Date().toISOString();
          const sync = { source: 'local-preview', revision: 0, contentHash, collectedAt: inventory.collectedAt, heartbeatAt: nowIso, receivedAt: nowIso, staleAfterSeconds: 180, stale: false };
          send(200, JSON.stringify({ schemaVersion: 1, inventory: route.endsWith('/meta') ? { collectedAt: inventory.collectedAt, sources: inventory.sources, machines: inventory.machines } : inventory, sync }));
        } catch { send(503, JSON.stringify({ error: 'local_preview_unavailable' })); }
      });
    },
  };
}
