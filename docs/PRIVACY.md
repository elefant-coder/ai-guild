# Privacy: what leaves your machine

AI Guild exists to look at your own environment, so the collector is deliberately blind to anything that is not a name or a number.

## Collected (names and metadata)

1. **Names of things**: model slugs, CLI names and their `--version` first line, MCP server names, skill names and their `description` frontmatter, agent names and descriptions, hook event names, rule file names, scheduler labels.
2. **Where they were found**: display paths with your home folder replaced by `~` (e.g. `~/.claude/agents/quill.md`).
3. **Scheduler state**: schedule shape (interval seconds, calendar fields, cron expression), loaded/running flags, last exit code, PID presence (not the PID value).
4. **Machine facts**: OS name and version, CPU architecture, counts of jobs/skills, which runtimes exist.
5. **Token counts**: per-day input/output/cached totals per provider, session counts, an irreversible hash of message ids in a private local cache used only to deduplicate.
6. **Timestamps** of when each check ran.

## Never collected

- Values of config keys: commands, arguments, URLs, environment variables, headers, tokens, API keys, OAuth records.
- Contents of any file other than the frontmatter of `SKILL.md` and agent definition files (and only the `name`, `description`, `model` keys).
- Conversation text, prompts, message bodies, file contents of your projects.
- Absolute home paths (`/Users/...`, `/home/...`). The server rejects uploads containing them.
- Anything under `~/.ssh`, `*.env`, `auth.json`, `credentials*`.

## Anonymising

Jobs whose label matches `collector.jobs.privatePatterns` (fnmatch) are published as `automation-private-<hash>` with a generic name and no label or file name. `excludePatterns` drops them entirely. `includePatterns` limits the scan to what you list.

## Enforcement, not just policy

- `collector/validate.py` checks every scan for the allowlist and for secret-looking strings before upload.
- The Worker re-validates every field (`server/index.js`, `server/stats-api.js`, `server/character-api.js`) and rejects unknown keys, keys that look like `password`, `token`, `env`, `args`, `payload`, and values matching key patterns (`sk-…`, `xox…`, `AIza…`, `ghp_…`, `Bearer …`, private keys, home paths).
- Reads require the passphrase session; writes require a separate bearer secret. Neither can do the other's job.
- All responses are `private, no-store`; the login page and app are `noindex`.

## Your copies

`guild.config.json`, `data/`, `generated/` and generated character art are git-ignored. If you fork and publish, nothing personal is committed by default.
