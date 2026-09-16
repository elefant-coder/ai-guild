# AI Guild

**Your AI environment as a tiny game world.** Agents become companions, skills and MCP servers become gear, scheduled jobs become quests, and your token usage becomes a level. Self-hosted, private, and shaped by you: the setup is an interview run by your own coding agent (Claude Code, Codex, …), which asks what your guild should look like and who lives in it, then scans your machine and builds it.

日本語の説明は [README.ja.md](README.ja.md) にあります。

<p align="center"><em>Home · Companions · Gear · Automation quests · Player HUD — on desktop and phone.</em></p>

## What it shows

| Category | Where it comes from (names and metadata only) |
|---|---|
| Models | Codex model cache, Claude Code settings, Gemini / Goose / Grok configs |
| CLIs | `which` + `--version` for a configurable list (claude, codex, gemini, gh, wrangler, ffmpeg, …) |
| Connections | MCP server **names** from Codex, Claude Code (global + per project), `.mcp.json`, enabled plugins |
| Skills | every `SKILL.md` under your shared, Codex and Claude skill roots and plugin caches |
| Agents | `~/.claude/agents/*.md`, plugin agents, `~/.codex/agents/*.toml` — with a title, strengths and an example request you write during setup |
| Harness | Claude hooks (event names), `~/.claude/rules/*.md`, presence of `CLAUDE.md` / `AGENTS.md` |
| Automation | launchd agents (macOS), `crontab`, systemd user timers — schedule, loaded/running, last exit code |
| Usage | Codex and Claude Code session logs → tokens per day, lifetime level |

Everything is **read-only** and **metadata-only**. See [docs/PRIVACY.md](docs/PRIVACY.md) for the exact list of what is and is not collected. The server rejects anything outside the allowlist and anything that looks like a secret.

## Quick start

```sh
git clone https://github.com/elefant-coder/ai-guild.git
cd ai-guild
npm install
npm run dev          # opens a demo guild with fictional data at http://127.0.0.1:4317
```

Then open the folder in **Claude Code** or **Codex** and say:

> set up AI Guild for me

The agent follows [SETUP.md](SETUP.md): it asks for your language, guild name, visual preset (`lavender-toybox`, `midnight-arcade`, `paper-atelier`, `pixel-dungeon`, or your own), what the companions are (animals, robots, chibi adventurers, plush mascots, your pet…), which machines to scan, whether to deploy, and which job names to anonymise. It then writes `guild.config.json`, scans your machine, generates character art if it has an image tool, previews the result with you, and deploys to Cloudflare Workers behind a passphrase.

Prefer to do it by hand? [docs/CUSTOMIZING.md](docs/CUSTOMIZING.md) and [docs/DEPLOY.md](docs/DEPLOY.md) cover the same steps.

## Requirements

- Node 20+, Python 3.11+ (standard library only; Pillow optional for resizing art)
- macOS or Linux for the collector (launchd / cron / systemd scanning); the site itself runs anywhere
- A free Cloudflare account for hosting, or run it locally only

## How it fits together

```
your machine                              Cloudflare
┌───────────────────────────┐             ┌──────────────────────────────┐
│ collector/scan.py  ──┐    │  HTTPS +    │ Worker: passphrase login,    │
│ collector/usage.py ──┼─► sync.py ──────►│ signed cookie, validation    │
│ collector/art.py   ──┘    │  bearer     │ Durable Object (SQLite):     │
│ guild.config.json          │             │ inventory · stats · art      │
└───────────────────────────┘             └──────────────┬───────────────┘
                                                          │ private JSON
                                                   React UI (phone-first)
```

- **Collector** (Python): scans, validates, and uploads with a bearer secret. Re-scans on config changes and heartbeats every 30 s when run as a background job.
- **Worker**: one owner, one passphrase, one signed HttpOnly cookie. A browser session can never write; the collector can never read.
- **UI**: React + Vite, two languages shipped (`en`, `ja`), theme tokens from your config, deterministic robot placeholders until your own art arrives.

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Make it yours

- `guild.config.json` — name, player, language, machines, theme colours, art style, featured companions, agent roles, brand overrides, collector roots and privacy patterns. Schema: `docs/guild.config.schema.json`.
- `presets/` — drop-in theme + art bundles.
- `src/locales/` — add a language by copying `en.json`.
- `collector/art.py plan` — prompts for every companion in your chosen style; feed them to any image generator and `import` the results.

## Commands

| | |
|---|---|
| `npm run dev` | local preview (real scan if `data/inventory.json` exists, else the demo) |
| `npm run scan` · `npm run usage` | write `data/inventory.json` / `data/stats.json` |
| `npm run validate` | check the scan against the schema and secret patterns |
| `npm test` · `npm run test:collector` | Worker tests · collector tests |
| `npm run deploy` · `npm run secrets` | build + deploy · set passphrase and secrets |
| `python3 collector/sync.py --run` | continuous sync (or `node scripts/install-scheduler.mjs`) |
| `python3 collector/art.py plan|import|push|status` | character art pipeline |

## License

MIT. Third-party logos in `public/art/brands/` belong to their owners and are used for identification only.
