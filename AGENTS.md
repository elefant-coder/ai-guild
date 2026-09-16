# AI Guild — instructions for Codex and other coding agents

**AI Guild** is an open-source, self-hosted, game-style catalog of a person's AI environment (agents, skills, MCP connections, CLIs, models, scheduled jobs, token usage), protected by a passphrase.

## Setting it up for the user
Follow `SETUP.md` step by step. It interviews the user about language, guild name, visual preset, what the companion characters look like, hosting and privacy, then scans the machine, generates art, previews, deploys. Ask taste questions in short blocks; never choose the look silently unless the user says “just pick”.

If you have a built-in image generation tool, use it in §5.3 of `SETUP.md` to create the companion art from `data/art-plan.json`, then import each file with `python3 collector/art.py import --id <id> --file <png>`.

## Layout
- `src/` — React + Vite UI. All user-facing text is in `src/locales/{en,ja}.json`. Theme tokens are applied from `guild.config.json` at runtime (`src/config.ts`).
- `server/` — Cloudflare Worker (login, session cookie, Durable Object SQLite, strict schema validation). `npm test`.
- `collector/` — Python 3.11+ read-only scanners and the sync/art tools. `python3 -m unittest discover -s collector/tests -t .`
- `guild.config.example.json` → user copy `guild.config.json` (git-ignored) → `npm run prepare-config` → `generated/`.
- `presets/` — theme + art bundles. `docs/` — schema, deploy, privacy, customizing, architecture.

## Rules
- Metadata only. Never collect values, command arguments, environment, message bodies, secrets, or absolute home paths. New collector fields need server validation and tests.
- Never open or print `~/.ssh`, `*.env`, `auth.json`, token or credential files.
- Never commit `guild.config.json`, `data/`, `generated/`, or generated art.
- Ask before deploying, uploading, installing background jobs, or spending money.
- Keep strings in locale files; keep both languages in sync.
- Verify with `npm run build`, `npm test`, the collector tests and an actual preview before reporting done.
