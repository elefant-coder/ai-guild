# Deploying to Cloudflare Workers

AI Guild runs as one Worker with a SQLite-backed Durable Object. The free plan is enough for one person.

## 1. Build and deploy

```sh
npx wrangler login          # opens a browser once
npm run deploy              # prepare-config → tsc → vite build → wrangler deploy
```

Wrangler prints the URL, typically `https://ai-guild.<your-subdomain>.workers.dev`. Change the Worker name in `wrangler.jsonc` if you want another subdomain, or attach a custom domain in the Cloudflare dashboard.

Until secrets are set, every request answers `503 This guild is not configured yet`.

## 2. Secrets

```sh
npm run secrets
```

The script asks for a **passphrase** (the only thing you type to open the site; 8+ characters), generates a 32-byte **session secret** and a 32-byte **ingest secret**, stores them with `wrangler secret put`, and writes the collector side:

- `~/.config/ai-guild/ingest.secret` (mode 0600)
- `~/.config/ai-guild/sync.json` → `{ "endpoint": "https://<url>/api/inventory", "ingestSecretFile": "~/.config/ai-guild/ingest.secret" }`

Run it again to rotate everything. Old browser sessions become invalid immediately.

Manual equivalent:

```sh
npx wrangler secret put GUILD_VIEW_PASSWORD
npx wrangler secret put GUILD_SESSION_SECRET
npx wrangler secret put GUILD_INGEST_SECRET
```

## 3. First sync

```sh
python3 collector/sync.py --check     # "ready" or the reason it is not
python3 collector/sync.py --once      # scan + upload inventory and stats
python3 collector/art.py push         # upload any local character art
```

Open the URL: the login page should appear; after the passphrase, your inventory. Verify from the outside that reads are closed:

```sh
curl -s -o /dev/null -w "%{http_code}\n" https://<url>/api/inventory     # 401
curl -s -o /dev/null -w "%{http_code}\n" https://<url>/                  # 302 → /login
```

## 4. Keep it in sync

```sh
node scripts/install-scheduler.mjs              # launchd (macOS) or systemd --user (Linux)
node scripts/install-scheduler.mjs --uninstall
```

The job runs `collector/sync.py --run`: every 30 s it fingerprints your config files (names, sizes, mtimes only), rescans what changed, uploads a new revision or a heartbeat, and refreshes usage stats once a minute. Logs: `~/.local/state/ai-guild/`.

## 5. Updating

```sh
git pull && npm install && npm run deploy
```

The Durable Object keeps your data across deploys. Schema changes are additive (`CREATE TABLE IF NOT EXISTS`).

## Troubleshooting

| Symptom | Check |
|---|---|
| `503` on every page | secrets missing → `npm run secrets` |
| Login works, page says “Waiting for the first sync” | `python3 collector/sync.py --once` and read its output |
| `sync.py --check` says the secret file mode is wrong | `chmod 600 ~/.config/ai-guild/ingest.secret` |
| `409 out_of_order_revision` in logs | the collector rebases automatically on the next cycle; if it persists, delete `~/.local/state/ai-guild/sync-state.json` |
| Art shows robots instead of your images | `python3 collector/art.py status`, then `push` |
