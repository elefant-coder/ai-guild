# AI Guild — instructions for Claude Code

You are working inside **AI Guild**, an open-source, self-hosted, game-style catalog of a person's AI environment.

## If the user asks to set it up
Follow `SETUP.md` exactly. It is an interview-driven flow: the user chooses the language, the guild name, the visual preset, what the companion characters are, hosting and privacy. Ask in short blocks, apply their answers, verify by running things. Never decide taste for them unless they say “just pick”.

## Repository map
- `src/` React UI (Vite). Strings live in `src/locales/*.json`; theme tokens come from `guild.config.json` via `src/config.ts`.
- `server/` Cloudflare Worker: passphrase login, signed cookie, Durable Object SQLite storage, strict validation of everything the collector uploads. Tests: `npm test`.
- `collector/` Python 3.11+ scripts that scan the local machine (read-only) and push metadata: `scan.py`, `usage.py`, `sync.py`, `art.py`, `validate.py`, `demo.py`. Tests: `python3 -m unittest discover -s collector/tests -t .`
- `guild.config.example.json` — every tunable. The user's copy is `guild.config.json` (git-ignored). `npm run prepare-config` merges them into `generated/`.
- `presets/` — palette + art-style bundles the setup can merge into the config.
- `docs/` — schema, deploy, privacy, customizing, architecture.

## Rules
- Privacy is the product. Collect names and metadata only; never values, arguments, env, message bodies, secrets, or absolute home paths. If you add a collector field, add server validation and a test.
- Do not read or print the contents of `~/.ssh`, `*.env`, `auth.json`, token or credential files.
- Do not commit `guild.config.json`, `data/`, `generated/`, or generated character art.
- Ask before deploying, uploading, installing background jobs, or anything that costs money.
- Keep the UI translatable: no hard-coded user-facing strings in components; add keys to both locale files.
- Run `npm run build`, `npm test` and the collector tests before claiming something works. Open the preview when you change UI.
