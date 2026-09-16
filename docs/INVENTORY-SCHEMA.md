# Inventory schema (v1)

This is the contract between the **collector** (runs on your machine) and the **site** (Cloudflare Worker + browser UI).
Everything the site shows comes from this JSON. Nothing else is ever uploaded.

## Envelope (`POST /api/inventory`)

```json
{
  "schemaVersion": 1,
  "source": "collector",
  "revision": 12,
  "collectedAt": "2026-01-01T00:00:00Z",
  "heartbeatAt": "2026-01-01T00:00:30Z",
  "inventory": { ...Inventory }
}
```

- `source` is always the literal string `collector` (one aggregator per deployment).
- `revision` is a monotonically increasing integer. The server rejects out-of-order revisions with `409`.
- `heartbeatAt >= collectedAt`. Both are ISO-8601 UTC strings.

## Heartbeat (`POST /api/inventory/heartbeat`)

Sent when nothing changed. Carries only freshness metadata plus the `contentHash` the server returned last time.

```json
{ "schemaVersion": 1, "source": "collector", "revision": 12, "contentHash": "<sha256 hex>",
  "collectedAt": "...", "heartbeatAt": "...", "sources": [...], "machines": {...} }
```

## Inventory

```ts
interface Inventory {
  collectedAt: string;            // ISO UTC
  items: Item[];                  // max 2,500
  sources: Source[];              // max 20 – one per collector module
  machines: Record<MachineId, Machine>;
  notes: string[];                // max 100, each ≤ 2,000 chars – coverage caveats shown in the UI
}

type MachineId = string;          // ^[a-z0-9-]{1,32}$ – must match guild.config.json "machines[].id"

interface Source { name: string; collectedAt: string; count: number; scope?: string }

interface Machine {
  id?: MachineId;
  status?: 'observed' | 'configured' | 'attention' | 'unknown' | 'unavailable';
  statusLabel?: string;
  checkedAt?: string;             // ISO UTC
  architecture?: string;          // e.g. arm64
  os?: string;                    // e.g. "macOS 15.5", "Ubuntu 24.04"
  jobCount?: number;              // scheduled jobs found
  loadedJobCount?: number;
  runningJobCount?: number;
  skillCount?: number;
  runtimes?: string[];            // e.g. ["claude", "codex"]
  reachability?: string;
  stale?: boolean;
  lastAttemptAt?: string;
  lastSuccessfulAt?: string;
}
```

### Item

```ts
type Category = 'model' | 'cli' | 'connection' | 'agent' | 'harness' | 'skill' | 'automation';
type Status   = 'observed' | 'configured' | 'attention' | 'unknown';

interface Item {
  id: string;                     // ^[A-Za-z0-9@-]{1,160}$, unique. Convention: "<category>-<slug>"
  name: string;                   // ≤ 400
  category: Category;
  summary: string;                // ≤ 4,000 – one or two plain sentences
  tags: string[];                 // ≤ 50 × ≤ 120 chars
  status: Status;
  statusLabel: string;            // ≤ 500 – human sentence explaining the status
  machines: MachineId[];          // where this item was found
  runtimes: string[];             // e.g. ["claude"], ["codex","mcp"], ["launchd"]
  evidence: Evidence[];           // 1..50 – what was checked, where, when
  relatedIds?: string[];          // other item ids
  command?: string;               // ≤ 1,000 – a *read-only* command the user can run to verify
  schedule?: Schedule;            // automation only
  meta?: Meta;
}

interface Evidence { source: string; detail: string; checkedAt: string }   // ≤500 / ≤1,000 / ISO

interface Schedule {
  kind: 'calendar' | 'interval' | 'resident' | 'ondemand' | 'other';
  label: string;                  // ≤ 300
  intervalSeconds?: number;
  calendar?: object | object[];   // launchd StartCalendarInterval shape, or {"expression": "*/5 * * * *"} for cron
  loaded?: boolean;               // registered with the scheduler
  running?: boolean;              // a process exists right now
  lastExitCode?: number;
}
```

### `meta` – allowed keys only

The server rejects any other key (and any key that looks like a secret: `password`, `token`, `env`, `args`, `payload`, ...).

| key | type | used for |
|---|---|---|
| `title` | string | agent class label shown under the portrait (e.g. "Frontend wizard") |
| `strengths` | string[] | agent strengths (2–4 short phrases) |
| `exampleTask` | string | one example request the user can copy |
| `role` | string | one-line role description |
| `origin` | string | where the definition came from (plugin, personal, native) |
| `modelPolicy` | string | model configured in the definition file, if any |
| `variants` | object[] | multiple definition files for the same agent |
| `descriptionOriginal` | string | original description text from a SKILL.md / agent file |
| `descriptionOriginalVariants` | object[] | same, per source |
| `sources` | object[] | `{kind,label}` pairs for skills found in several roots |
| `sourceCount` | number | |
| `sourceFile` | string | display path (`~/...`), never an absolute `/Users/...` or `/home/...` path |
| `label` | string | scheduler label (launchd Label / cron comment) |
| `runAtLoad` | boolean | |
| `event` | string | hook event name |
| `hookGroups` | number | |
| `provider` | string | model provider |
| `model` | string | model slug |
| `effort` | string | |
| `contextWindow` | number | |
| `defaultReasoning` | string | |
| `executable` | string | display path of a CLI |
| `enabled` | boolean | |
| `toolCount` | number | MCP tools advertised |
| `summarySource` | string | how the summary was derived |
| `sessionAvailability` | string | |
| `visibleToCli` | boolean | |
| `rosterScope` | string | `execution-snapshot` items are hidden from the roster |
| `schedulerId` | string | opaque id for anonymized jobs |

## Hard privacy rules (enforced server-side too)

- No absolute home paths. Display paths as `~/...`.
- No environment values, command arguments, tokens, cookies, message bodies, file contents.
- The server scans the whole payload for secret-looking strings (`sk-…`, `xox…`, `AIza…`, `Bearer …`, private keys, `/Users/`, `/home/`) and rejects the upload.
- Jobs whose label matches `collector.jobs.privatePatterns` are published with an opaque id (`automation-private-<hash>`), a generic name and no `meta.label` / `meta.sourceFile`.

## Stats (`POST /api/stats`)

```ts
interface Stats {
  schemaVersion: 1;
  collectedAt: string;                 // ISO UTC
  scope: string;                       // ≤ 80, e.g. "This machine"
  timezone: string;                    // IANA, e.g. "Asia/Tokyo" – days are bucketed in this zone
  totals: Tokens; today: Tokens;
  days: (Tokens & { date: 'YYYY-MM-DD' })[];   // exactly 7, consecutive, last = today in `timezone`
  providers: { id: string; name: string; totals: Tokens; today: Tokens; sessions: number }[];  // 1..6
  coverage: { startedAt: string | null; notes: string[] };   // ≤ 8 notes
}
interface Tokens { input: number; output: number; cachedInput: number; total: number }  // cachedInput ≤ input, total = input + output
```

Provider totals must sum to `totals`, and `today` must equal `days[6]`.

## Characters (`POST /api/characters`, `POST /api/characters/:id/image`)

```json
{ "schemaVersion": 1, "promptVersion": "<style version string>", "characters": [ { "id": "agent-quill", "category": "agent", "contentHash": "<sha256 of descriptor>" } ] }
```

Then upload the image bytes (`image/webp` or `image/png`, ≤ 512 KiB) with header `X-Character-Descriptor: <same sha256>`.
Ids must exist in the current inventory with the same category.
