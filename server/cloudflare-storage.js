// SQLite-backed Durable Object that stores the inventory, character art and usage stats for one guild.
import { DurableObject } from 'cloudflare:workers';
import app from './index.js';
import { CHARACTER_SCHEMA, handleCharacterApi } from './character-api.js';
import { STATS_SCHEMA, handleStatsApi } from './stats-api.js';

export const INVENTORY_SCHEMA = `
  CREATE TABLE IF NOT EXISTS guild_inventory (
    source TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL,
    collected_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL
  )
`;
const rows = cursor => Array.from(cursor, row => ({ ...row }));

/** D1-compatible subset over the Durable Object SQL API, so the API handlers stay storage-agnostic. */
export function createD1Adapter(sql) {
  return { prepare(statement) { return { bind(...parameters) { return {
    async first() { return rows(sql.exec(statement, ...parameters))[0] ?? null; },
    async all() { return rows(sql.exec(statement, ...parameters)); },
    async run() {
      sql.exec(statement, ...parameters);
      // Read SQLite's change count before any unrelated statement can replace it.
      const changed = rows(sql.exec('SELECT changes() AS changes'))[0];
      return { meta: { changes: Number(changed?.changes ?? 0) } };
    },
  }; } }; } };
}

export class GuildStore extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.env = env;
    ctx.storage.sql.exec(INVENTORY_SCHEMA);
    ctx.storage.sql.exec(CHARACTER_SCHEMA);
    ctx.storage.sql.exec(STATS_SCHEMA);
    this.db = createD1Adapter(ctx.storage.sql);
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === '/api/stats') return handleStatsApi(request, this.env, this.db);
    if (path.startsWith('/api/characters')) return handleCharacterApi(request, this.env, this.db);
    // The outer worker authenticates and routes only API requests here.
    return app.fetch(request, { ...this.env, DB: this.db });
  }
}
