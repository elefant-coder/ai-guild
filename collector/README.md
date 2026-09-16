# Collector

Read-only scanners that turn your local AI setup into the JSON the AI Guild site
renders. Python 3.11 or newer, standard library only. Pillow is optional and is
used for resizing artwork.

Run every command from the repository root.

## Scripts

| Script | What it does |
| --- | --- |
| `scan.py` | Walks five modules and writes `data/inventory.json`. |
| `validate.py` | Checks an inventory or a statistics file against `docs/INVENTORY-SCHEMA.md` and the secret patterns. |
| `usage.py` | Aggregates local token usage into `data/stats.json`. |
| `sync.py` | Keeps the site in step: refresh, upload, heartbeat, statistics. |
| `art.py` | Plans, imports and publishes the portraits shown next to each entry. |
| `demo.py` | Rewrites the fictional demo data under `public/demo/`. |
| `common.py` | Configuration loading, display paths, atomic writes, subprocess wrapper. |

`scan.py` is deliberately a single self-contained file, because a remote machine
is scanned by piping this exact source into its interpreter.

## Modules

| Module | Reads |
| --- | --- |
| `runtime` | Model names from local CLI configuration, command line tools on `PATH` and their `--version` line, MCP server names, enabled plugins, pending sign-ins. |
| `skills` | Every `SKILL.md` under the configured skill roots, with its frontmatter name and description. |
| `agents` | Claude agent definitions, plugin agents, Codex agent profiles. |
| `harness` | Hook event names and group counts, rule documents, the presence of instruction files. |
| `automation` | launchd jobs, crontab schedule expressions, systemd user timers, plus loaded and running state. |

## What is never collected

- Environment values, command arguments, credentials, cookies, tokens.
- File contents: prompts, replies, session transcripts, job payloads, logs.
- Absolute home paths. Everything is displayed as `~/...`, and free text is
  scrubbed of home paths and secret-shaped strings before it is written.
- Labels of jobs matching `collector.jobs.privatePatterns`. Those are published
  with an opaque id, a generic name and no source file.

`usage.py` reads only timestamps and token counts, and stores message ids as
one-way hashes. `validate.py` refuses any document that still carries a
secret-looking string, so a mistake is caught before an upload.

## Running by hand

```sh
python3 collector/scan.py --out data/inventory.json
python3 collector/scan.py --stdout --modules runtime,skills   # print a partial scan
python3 collector/validate.py data/inventory.json

python3 collector/usage.py --out data/stats.json
python3 collector/validate.py --stats data/stats.json

python3 collector/sync.py --check     # is publishing configured?
python3 collector/sync.py --once      # one refresh and upload
python3 collector/sync.py --run       # loop every 30 seconds

python3 collector/art.py plan --include-tools
python3 collector/art.py import --id agent-compass --file portrait.png
python3 collector/art.py status
python3 collector/art.py push

python3 collector/demo.py
python3 -m unittest discover -s collector/tests -t .
```

## Configuration

Copy `guild.config.example.json` to `guild.config.json` and edit it. The
collector reads these keys.

| Key | Meaning |
| --- | --- |
| `timezone` | IANA zone used to bucket usage days. |
| `machines[]` | `{id, label, kind}`. `kind: "local"` is this machine, `kind: "ssh"` adds `host` and is scanned over SSH. Ids match `^[a-z0-9-]{1,32}$`. |
| `roles` | Optional per-agent `{title, strengths, exampleTask}`. Without it, both are derived from the agent's own description. |
| `collector.roots` | Where Claude, Codex and shared skills live. |
| `collector.projectRoots` | Folders searched three levels deep for `.mcp.json`. |
| `collector.clis` | Command line tools to look for. Only `<cli> --version` is ever run. |
| `collector.jobs` | `includePatterns`, `excludePatterns` and `privatePatterns`, matched against a scheduler label. |
| `collector.usage.providers` | Which local logs to aggregate: `codex`, `claude`. |
| `art` | `style`, `characterConcept`, `subjects`, `toolStyle`, `constraints`, `dailyLimit`. |
| `theme.preset` | Becomes the prompt version, `<preset>-v1`. |

Publishing is configured separately, outside the repository, in
`~/.config/ai-guild/sync.json`:

```json
{
  "endpoint": "https://your-worker.example/api/inventory",
  "ingestSecretFile": "~/.config/ai-guild/ingest.secret"
}
```

The secret file must be `chmod 600`. The endpoint must be HTTPS; `--allow-http`
exists only for a local test server. Redirects are refused, and request headers
and bodies are never written to `~/.local/state/ai-guild/sync.log`.

## State

| Path | Contents |
| --- | --- |
| `data/parts/<module>.json` | Last result per scan module, so `sync.py` only re-runs what changed. |
| `~/.local/state/ai-guild/usage/state.json` | Resumable usage scan cache, mode 600. |
| `~/.local/state/ai-guild/sync-state.json` | Revision, content hash and retry state, mode 600. |
| `~/.local/state/ai-guild/sync.log` | Status lines, rotated at 256 KiB. |
| `~/.local/state/ai-guild/art-push.json` | Which portraits were already uploaded. |
