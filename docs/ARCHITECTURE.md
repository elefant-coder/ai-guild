# Architecture

Three parts, one contract (`docs/INVENTORY-SCHEMA.md`).

## Collector (`collector/`, Python 3.11+, stdlib)

| script | job |
|---|---|
| `scan.py` | read-only inventory: runtime (models, CLIs, connections), skills, agents, harness, automation, machines. Writes `data/inventory.json`. `--machine <id> --stdout` is used when the same script is streamed to a remote host over SSH. |
| `usage.py` | token usage from Codex and Claude Code session logs → `data/stats.json`. Deduplicates Claude message ids by hash, merges Codex session metadata, excludes fork-inherited snapshots, resumes large files at byte offsets. |
| `validate.py` | schema + secret-pattern check for inventory and stats files. |
| `sync.py` | the loop: fingerprint watched paths → rescan on change (runtime ≤1/min) → upload envelope (revision +1) or heartbeat → usage every 60 s. Atomic state, lock file, bounded log, exponential backoff, 409 rebase. |
| `art.py` | `plan` (prompts from `guild.config.json`), `import` (validate, resize, WebP, manifest), `push` (register descriptors, upload bytes), `generate` (optional local Codex CLI), `status`. |
| `demo.py` | fictional inventory/stats for the public demo and tests. |

Config lives outside the repo: `~/.config/ai-guild/sync.json` and `ingest.secret` (0600); state in `~/.local/state/ai-guild/`.

## Worker (`server/`)

```
request ─► cloudflare.js (outer)
            │  /login, /logout: passphrase ⇄ HMAC-signed, host-bound, HttpOnly cookie
            │  strips x-guild-* headers; sets x-guild-authenticated: owner after cookie check
            │  POST ingest routes: pass through (bearer checked inside)
            │  everything else: cookie or 302 /login (401 for /api/*)
            ├─► static assets (ASSETS binding) with private, no-store + CSP
            └─► Durable Object "primary" (cloudflare-storage.js → GuildStore)
                   ├─ index.js          /api/inventory, /meta, /heartbeat   (guild_inventory)
                   ├─ character-api.js  /api/characters, /:id/image          (guild_characters, BLOBs)
                   └─ stats-api.js      /api/stats                           (guild_stats)
```

Validation is allowlist-based: unknown keys, forbidden key names, oversize values, out-of-order revisions or timestamps and secret-looking strings are all rejected. Content hashes exclude freshness fields so a heartbeat can confirm “nothing changed” cheaply.

## UI (`src/`)

React 19 + Vite. `main.tsx` is the app shell (pages: home, agents, library, automations, favorites, sources, map). `ui.tsx` holds shared pieces (rows, detail dialog, schedule formatting). `characters.tsx` polls `/api/characters` and decides per item whether to show an official logo, uploaded art, a shared tool image, or the built-in robot portrait. `PlayerStatus.tsx` renders the HUD from `/api/stats`. `config.ts` exposes the merged config and pushes theme tokens to `:root`; `i18n.ts` picks a locale file.

Polling: inventory meta every 10 s (full fetch only when the hash changes), characters every 10 s, stats every 10 s, all paused when the tab is hidden.

## Data flow for a new agent

1. You add `~/.claude/agents/scout.md`.
2. `sync.py` sees the fingerprint change, runs `scan.py`, uploads revision N+1.
3. The browser's next meta poll sees a new hash and fetches the inventory; “scout” appears with a robot portrait.
4. `art.py plan` lists `agent-scout` with a prompt; you generate an image and `import` + `push` it.
5. The character manifest updates; the portrait swaps in without a reload and “scout” shows under “New companions”.
