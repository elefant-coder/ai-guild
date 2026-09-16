#!/usr/bin/env python3
"""Write the fictional demo data the public site shows to visitors.

Everything here is invented: the agents, tools, connections, skills and jobs
describe a plausible setup that belongs to nobody.  Dates are stamped at write
time, so a freshly built site never looks abandoned.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):  # executed as a file rather than a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.common import REPO_ROOT, slug, to_iso, write_json_atomic  # noqa: E402

DEMO_DIR = REPO_ROOT / "public" / "demo"
MAIN = "main"
LAPTOP = "laptop"

AGENTS: list[dict[str, Any]] = [
    {
        "name": "compass", "title": "Systems architect",
        "summary": "Maps a change across the whole system before anyone writes code.",
        "strengths": ["Architecture reviews", "Dependency mapping", "Migration plans"],
        "example": "Compare two ways to split this service and recommend one.",
        "model": "claude-opus-5", "machines": [MAIN],
    },
    {
        "name": "quill", "title": "Editor",
        "summary": "Turns rough notes into copy that reads like a person wrote it.",
        "strengths": ["Release notes", "Documentation", "Tone and structure"],
        "example": "Rewrite this changelog for people who do not know the codebase.",
        "model": "claude-sonnet-5", "machines": [MAIN, LAPTOP],
    },
    {
        "name": "forge", "title": "Builder",
        "summary": "Implements a scoped change and keeps the tests green.",
        "strengths": ["Feature work", "Refactoring", "Test coverage"],
        "example": "Add pagination to the results endpoint and cover it with tests.",
        "model": "claude-sonnet-5", "machines": [MAIN],
    },
    {
        "name": "scout", "title": "Researcher",
        "summary": "Gathers current sources and reports what actually holds up.",
        "strengths": ["Source gathering", "Comparison tables", "Cited summaries"],
        "example": "Find out which queue libraries still ship security updates.",
        "model": "gemini-2.5-pro", "machines": [MAIN],
    },
    {
        "name": "sentinel", "title": "Security reviewer",
        "summary": "Reads a diff for the mistakes that become incidents.",
        "strengths": ["Threat modelling", "Dependency review", "Secret handling"],
        "example": "Review this authentication change before it ships.",
        "model": "claude-opus-5", "machines": [MAIN, LAPTOP],
    },
    {
        "name": "muse", "title": "Interface designer",
        "summary": "Designs screens and states, then hands over the tokens to build them.",
        "strengths": ["Layout and hierarchy", "Design tokens", "Accessibility"],
        "example": "Draft the empty, loading and error states for this table.",
        "model": "claude-sonnet-5", "machines": [LAPTOP],
    },
    {
        "name": "ledger", "title": "Numbers analyst",
        "summary": "Reconciles figures and shows the arithmetic behind each total.",
        "strengths": ["Reconciliation", "Forecasts", "Spreadsheet models"],
        "example": "Check last quarter's totals against the exported statements.",
        "model": "gpt-5-codex", "machines": [MAIN],
    },
    {
        "name": "prism", "title": "Image wrangler",
        "summary": "Prepares, crops and converts image assets for every surface.",
        "strengths": ["Batch conversion", "Sprite sheets", "Asset naming"],
        "example": "Convert these screenshots to WebP at two sizes.",
        "model": "gemini-2.5-pro", "machines": [LAPTOP],
    },
]

MODELS: list[tuple[str, str, str, int, list[str]]] = [
    ("claude-opus-5", "claude", "Selected as the default model for deep work.", 200_000, [MAIN, LAPTOP]),
    ("claude-sonnet-5", "claude", "Selected as the everyday model for scoped tasks.", 200_000, [MAIN, LAPTOP]),
    ("gpt-5-codex", "codex", "Offered by the local Codex model list.", 400_000, [MAIN]),
    ("gemini-2.5-pro", "gemini", "Named in the local Gemini configuration.", 1_000_000, [MAIN]),
]

CLIS: list[tuple[str, str, str, list[str]]] = [
    ("claude", "2.1.4", "Coding agent in the terminal.", [MAIN, LAPTOP]),
    ("codex", "0.48.0", "Second coding agent, used for long batch work.", [MAIN]),
    ("gemini", "1.9.2", "Third agent, used for very large inputs.", [MAIN]),
    ("gh", "2.63.0", "GitHub from the command line.", [MAIN, LAPTOP]),
    ("wrangler", "4.2.1", "Deploys the workers that serve this site.", [MAIN]),
    ("ffmpeg", "7.1", "Converts audio and video files.", [MAIN]),
    ("node", "22.9.0", "JavaScript runtime for the build.", [MAIN, LAPTOP]),
    ("python3", "3.12.4", "Runs the collector itself.", [MAIN, LAPTOP]),
]

CONNECTIONS: list[tuple[str, str, str, str, list[str], list[str]]] = [
    ("github", "Repositories, issues and pull requests.", "configured", "Registered in local configuration. Connection and authentication were not tested.", ["mcp", "codex"], [MAIN]),
    ("notion", "Pages and databases in the team workspace.", "configured", "Registered in local configuration. Connection and authentication were not tested.", ["mcp", "claude"], [MAIN, LAPTOP]),
    ("slack", "Channel history and message drafts.", "attention", "Listed in the authentication cache. Sign-in may be required before use.", ["mcp", "claude", "authentication"], [MAIN]),
    ("playwright", "Drives a browser for checks and screenshots.", "configured", "Declared in a project file. Connection and authentication were not tested.", ["mcp", "project"], [MAIN]),
    ("postgres", "Read-only queries against the staging database.", "configured", "Registered for a project scope. Connection and authentication were not tested.", ["mcp", "project"], [MAIN]),
    ("sentry", "Error groups and release health.", "configured", "Enabled in local settings. The tools it provides were not contacted.", ["plugin", "claude"], [LAPTOP]),
]

SKILLS: list[tuple[str, str, str, list[str]]] = [
    ("release-notes", "Turn merged pull requests into readable release notes.", "shared", [MAIN, LAPTOP]),
    ("incident-review", "Write up an incident with a timeline and follow-up actions.", "shared", [MAIN]),
    ("api-contract", "Draft and review an API contract before implementation starts.", "shared", [MAIN]),
    ("test-plan", "Produce a test plan from a change description.", "codex", [MAIN]),
    ("db-migration", "Plan a schema migration with a rollback path.", "codex", [MAIN]),
    ("perf-budget", "Set and check a performance budget for a page.", "claude", [LAPTOP]),
    ("design-tokens", "Extract design tokens from a mockup and keep them in sync.", "claude", [LAPTOP]),
    ("screenshot-tour", "Capture a guided screenshot tour of a running app.", "plugin", [LAPTOP]),
    ("data-cleanup", "Normalise a messy export into a tidy table.", "shared", [MAIN]),
    ("chart-builder", "Build a chart that answers one specific question.", "shared", [MAIN, LAPTOP]),
    ("meeting-notes", "Turn a transcript into decisions and owners.", "shared", [MAIN]),
    ("weekly-digest", "Summarise the week's work for people who were not there.", "shared", [MAIN]),
    ("dependency-audit", "Check dependencies for stale and risky versions.", "codex", [MAIN]),
    ("secret-scan", "Look for credentials that should never have been committed.", "codex", [MAIN]),
    ("cost-report", "Explain last month's infrastructure bill line by line.", "claude", [MAIN]),
    ("onboarding-guide", "Write the first-week guide for a new contributor.", "claude", [LAPTOP]),
    ("video-cutdown", "Cut a long recording into short clips with captions.", "plugin", [LAPTOP]),
    ("image-prep", "Resize and convert images for the web.", "plugin", [LAPTOP]),
    ("backup-check", "Verify that backups exist and can actually be restored.", "shared", [MAIN]),
    ("search-index", "Rebuild a search index and compare the results before and after.", "shared", [MAIN]),
]

HARNESS: list[tuple[str, str, str, str, dict[str, Any], list[str]]] = [
    ("harness-hook-sessionstart", "SessionStart", "Loads project context whenever a session begins.", "hook", {"event": "SessionStart", "hookGroups": 2}, [MAIN, LAPTOP]),
    ("harness-hook-pretooluse", "PreToolUse", "Checks risky commands before a tool runs.", "hook", {"event": "PreToolUse", "hookGroups": 3}, [MAIN]),
    ("harness-hook-stop", "Stop", "Files a short work note when a session ends.", "hook", {"event": "Stop", "hookGroups": 1}, [MAIN]),
    ("harness-rule-review-checklist", "review-checklist", "Every change is reviewed against correctness, tests and rollback.", "rules", {"sourceFile": "~/.claude/rules/review-checklist.md"}, [MAIN, LAPTOP]),
    ("harness-rule-writing-style", "writing-style", "Short sentences, no filler, one idea at a time.", "rules", {"sourceFile": "~/.claude/rules/writing-style.md"}, [MAIN]),
    ("harness-claude-instructions", "Claude instructions", "Global instruction file applied to every Claude Code session.", "instructions", {"sourceFile": "~/.claude/CLAUDE.md"}, [MAIN, LAPTOP]),
]

JOBS: list[dict[str, Any]] = [
    {"id": "automation-daily-digest", "name": "daily-digest", "summary": "Builds the morning digest from yesterday's work.",
     "schedule": {"kind": "calendar", "label": "Runs on a calendar schedule", "calendar": {"Hour": 7, "Minute": 30}, "loaded": True, "running": False, "lastExitCode": 0},
     "status": "observed", "statusLabel": "Loaded in the scheduler and waiting for its next trigger.", "machines": [MAIN]},
    {"id": "automation-inventory-sync", "name": "inventory-sync", "summary": "Keeps this site in step with the machine.",
     "schedule": {"kind": "resident", "label": "Kept running as a resident process", "loaded": True, "running": True, "lastExitCode": 0},
     "status": "observed", "statusLabel": "Loaded in the scheduler and running right now.", "machines": [MAIN]},
    {"id": "automation-usage-refresh", "name": "usage-refresh", "summary": "Recomputes the token usage snapshot.",
     "schedule": {"kind": "interval", "label": "Runs every 900 seconds", "intervalSeconds": 900, "loaded": True, "running": False, "lastExitCode": 0},
     "status": "observed", "statusLabel": "Loaded in the scheduler and waiting for its next trigger.", "machines": [MAIN]},
    {"id": "automation-backup-verify", "name": "backup-verify", "summary": "Restores a sample from last night's backup.",
     "schedule": {"kind": "calendar", "label": "Runs on a calendar schedule", "calendar": {"Weekday": 1, "Hour": 3}, "loaded": True, "running": False, "lastExitCode": 1},
     "status": "attention", "statusLabel": "The scheduler reported exit code 1 for the last run.", "machines": [MAIN]},
    {"id": "automation-link-check", "name": "link-check", "summary": "Checks the public site for broken links.",
     "schedule": {"kind": "calendar", "label": "Runs on a cron schedule", "calendar": {"expression": "0 6 * * 1"}, "loaded": True, "running": False},
     "status": "observed", "statusLabel": "Present in the crontab. The command text is not collected.", "machines": [MAIN]},
    {"id": "automation-photo-import", "name": "photo-import", "summary": "Imports and converts new screenshots.",
     "schedule": {"kind": "ondemand", "label": "Runs when loaded at login", "loaded": False, "running": False},
     "status": "configured", "statusLabel": "Job file found. It is not currently loaded in the scheduler.", "machines": [LAPTOP]},
    {"id": "automation-index-rebuild", "name": "index-rebuild", "summary": "Rebuilds the local search index overnight.",
     "schedule": {"kind": "interval", "label": "Runs every 21600 seconds", "intervalSeconds": 21_600, "loaded": True, "running": False, "lastExitCode": 0},
     "status": "observed", "statusLabel": "Loaded in the scheduler and waiting for its next trigger.", "machines": [LAPTOP]},
    {"id": "automation-log-rotate", "name": "log-rotate", "summary": "Trims local logs so they stay small.",
     "schedule": {"kind": "other", "label": "Trigger not captured by this scan", "loaded": True, "running": False},
     "status": "observed", "statusLabel": "Loaded in the scheduler with a trigger this scan does not read.", "machines": [MAIN]},
    {"id": "automation-private-4f2a91c0b7de", "name": "Private job", "summary": "Scheduled job registered with the user launchd scheduler.",
     "schedule": {"kind": "interval", "label": "Runs every 3600 seconds", "intervalSeconds": 3_600, "loaded": True, "running": False, "lastExitCode": 0},
     "status": "observed", "statusLabel": "Loaded in the scheduler and waiting for its next trigger.", "machines": [MAIN],
     "private": True},
]


def evidence(source: str, detail: str, checked_at: str) -> list[dict[str, str]]:
    """One evidence record, formatted as the schema expects."""
    return [{"source": source, "detail": detail, "checkedAt": checked_at}]


def build_items(checked_at: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every demo item, plus the per-module counts used for sources."""
    items: list[dict[str, Any]] = []

    for name, provider, summary, window, machines in MODELS:
        items.append({
            "id": "model-" + slug(name), "name": name, "category": "model", "summary": summary,
            "tags": [provider, "model"], "status": "configured", "statusLabel": "Named in a local configuration file.",
            "machines": machines, "runtimes": [provider],
            "evidence": evidence("~/." + provider + "/settings.json", "Model key found in configuration", checked_at),
            "meta": {"model": name, "provider": provider, "contextWindow": window},
        })

    for name, version, summary, machines in CLIS:
        items.append({
            "id": "cli-" + slug(name), "name": name, "category": "cli", "summary": summary,
            "tags": ["cli"], "status": "observed", "statusLabel": "Executable found on PATH at scan time.",
            "machines": machines, "runtimes": [name],
            "evidence": evidence(name + " --version", name + " " + version, checked_at),
            "command": name + " --version",
            "meta": {"executable": "~/.local/bin/" + name},
        })

    for name, summary, status, status_label, tags, machines in CONNECTIONS:
        items.append({
            "id": "connection-" + slug(name), "name": name, "category": "connection", "summary": summary,
            "tags": tags, "status": status, "statusLabel": status_label,
            "machines": machines, "runtimes": ["mcp"],
            "evidence": evidence("~/.config/mcp/registry.json", "Server name found in the registry", checked_at),
        })

    for agent in AGENTS:
        items.append({
            "id": "agent-" + slug(agent["name"]), "name": agent["name"], "category": "agent", "summary": agent["summary"],
            "tags": ["agent", "claude"], "status": "configured", "statusLabel": "Agent definition found locally. It was not started.",
            "machines": agent["machines"], "runtimes": ["claude"],
            "evidence": evidence("~/.claude/agents/" + agent["name"] + ".md", "Agent frontmatter read", checked_at),
            "meta": {
                "title": agent["title"], "role": agent["summary"], "strengths": agent["strengths"],
                "exampleTask": agent["example"], "origin": "personal",
                "modelPolicy": "Definition names model " + agent["model"], "model": agent["model"],
                "sourceFile": "~/.claude/agents/" + agent["name"] + ".md",
            },
        })

    for identifier, name, summary, kind, meta, machines in HARNESS:
        items.append({
            "id": identifier, "name": name, "category": "harness", "summary": summary,
            "tags": ["harness", kind], "status": "configured",
            "statusLabel": "Configured locally. Command text is not collected.",
            "machines": machines, "runtimes": ["claude"],
            "evidence": evidence(str(meta.get("sourceFile", "~/.claude/settings.json")), "Configuration read", checked_at),
            "meta": meta,
        })

    for name, summary, kind, machines in SKILLS:
        items.append({
            "id": "skill-" + slug(name), "name": name, "category": "skill", "summary": summary,
            "tags": [kind], "status": "configured", "statusLabel": "SKILL.md found locally. Execution was not attempted.",
            "machines": machines, "runtimes": ["claude" if kind in ("claude", "plugin") else "codex" if kind == "codex" else "shared"],
            "evidence": evidence("~/.agents/skills/" + name + "/SKILL.md", "SKILL.md found on disk", checked_at),
            "meta": {"sources": [{"kind": kind, "label": kind.title() + " skills"}], "sourceCount": 1, "descriptionOriginal": summary, "summarySource": "frontmatter"},
        })

    for job in JOBS:
        private = bool(job.get("private"))
        meta: dict[str, Any] = {"runAtLoad": job["schedule"]["kind"] == "ondemand"}
        if private:
            meta["schedulerId"] = job["id"]
        else:
            meta["label"] = "local." + job["name"]
            meta["sourceFile"] = "~/Library/LaunchAgents/local." + job["name"] + ".plist"
        items.append({
            "id": job["id"], "name": job["name"], "category": "automation", "summary": job["summary"],
            "tags": ["launchd", job["schedule"]["kind"]], "status": job["status"], "statusLabel": job["statusLabel"],
            "machines": job["machines"], "runtimes": ["launchd"],
            "evidence": evidence(
                "~/Library/LaunchAgents (label withheld)" if private else "~/Library/LaunchAgents/local." + job["name"] + ".plist",
                "Trigger metadata and scheduler state read", checked_at,
            ),
            "schedule": job["schedule"], "meta": meta,
        })

    counts = {
        "runtime": len(MODELS) + len(CLIS) + len(CONNECTIONS),
        "skills": len(SKILLS),
        "agents": len(AGENTS),
        "harness": len(HARNESS),
        "automation": len(JOBS),
    }
    return items, counts


def build_inventory(moment: datetime) -> dict[str, Any]:
    """The demo inventory document."""
    checked_at = to_iso(moment)
    earlier = to_iso(moment - timedelta(minutes=4))
    items, counts = build_items(checked_at)
    order = ("model", "cli", "connection", "agent", "harness", "skill", "automation")
    items.sort(key=lambda item: (order.index(item["category"]), item["name"].casefold()))
    scopes = {
        "runtime": "Models, command line tools and tool connections",
        "skills": "Skill definitions found in local skill roots",
        "agents": "Agent definitions found in local agent roots",
        "harness": "Hooks, rules and instruction files",
        "automation": "Scheduled jobs registered with the local scheduler",
    }
    return {
        "collectedAt": checked_at,
        "items": items,
        "sources": [{"name": name, "collectedAt": checked_at, "count": count, "scope": scopes[name]} for name, count in counts.items()],
        "machines": {
            MAIN: {
                "id": MAIN, "status": "observed", "statusLabel": "Scanned locally, read only.", "checkedAt": checked_at,
                "architecture": "arm64", "os": "macOS 15.5", "jobCount": sum(1 for job in JOBS if MAIN in job["machines"]),
                "loadedJobCount": sum(1 for job in JOBS if MAIN in job["machines"] and job["schedule"].get("loaded")),
                "runningJobCount": sum(1 for job in JOBS if MAIN in job["machines"] and job["schedule"].get("running")),
                "skillCount": sum(1 for skill in SKILLS if MAIN in skill[3]), "runtimes": ["claude", "codex", "gemini"],
            },
            LAPTOP: {
                "id": LAPTOP, "status": "observed", "statusLabel": "Scanned over SSH, read only.", "checkedAt": earlier,
                "architecture": "arm64", "os": "Ubuntu 24.04", "jobCount": sum(1 for job in JOBS if LAPTOP in job["machines"]),
                "loadedJobCount": sum(1 for job in JOBS if LAPTOP in job["machines"] and job["schedule"].get("loaded")),
                "runningJobCount": sum(1 for job in JOBS if LAPTOP in job["machines"] and job["schedule"].get("running")),
                "skillCount": sum(1 for skill in SKILLS if LAPTOP in skill[3]), "runtimes": ["claude"],
            },
        },
        "notes": [
            "This is demo data. Every agent, tool and job on this page is invented.",
            "A real scan reads names, versions and trigger metadata only. Prompts, file contents and credentials are never collected.",
            "Connection entries record only that a name is configured. Authentication and reachability were not tested.",
            "Jobs matching a private pattern are published with an opaque id and a generic name: 1 job.",
        ],
    }


def build_stats(moment: datetime) -> dict[str, Any]:
    """The demo usage snapshot: seven days across two providers."""
    shape = [
        (486_000, 61_000, 452_000, 214_000, 26_000, 198_000),
        (512_000, 58_000, 470_000, 61_000, 9_000, 54_000),
        (98_000, 12_000, 88_000, 342_000, 41_000, 316_000),
        (734_000, 92_000, 690_000, 288_000, 33_000, 262_000),
        (655_000, 77_000, 612_000, 402_000, 48_000, 371_000),
        (321_000, 44_000, 295_000, 176_000, 21_000, 160_000),
        (268_000, 31_000, 244_000, 129_000, 17_000, 118_000),
    ]
    days: list[dict[str, Any]] = []
    provider_days: dict[str, list[dict[str, int]]] = {"claude": [], "codex": []}
    for offset, row in enumerate(shape):
        date = (moment - timedelta(days=6 - offset)).date().isoformat()
        claude = {"input": row[0], "output": row[1], "cachedInput": row[2], "total": row[0] + row[1]}
        codex = {"input": row[3], "output": row[4], "cachedInput": row[5], "total": row[3] + row[4]}
        provider_days["claude"].append(claude)
        provider_days["codex"].append(codex)
        days.append({
            "date": date,
            "input": claude["input"] + codex["input"],
            "output": claude["output"] + codex["output"],
            "cachedInput": claude["cachedInput"] + codex["cachedInput"],
            "total": claude["total"] + codex["total"],
        })

    def total_of(rows: Sequence[dict[str, int]]) -> dict[str, int]:
        return {key: sum(row[key] for row in rows) for key in ("input", "output", "cachedInput", "total")}

    totals = {key: sum(day[key] for day in days) for key in ("input", "output", "cachedInput", "total")}
    today = {key: value for key, value in days[-1].items() if key != "date"}
    return {
        "schemaVersion": 1,
        "collectedAt": to_iso(moment),
        "scope": "This machine",
        "timezone": "UTC",
        "totals": totals,
        "today": today,
        "days": days,
        "providers": [
            {"id": "claude", "name": "Claude Code", "totals": total_of(provider_days["claude"]), "today": provider_days["claude"][-1], "sessions": 41},
            {"id": "codex", "name": "Codex", "totals": total_of(provider_days["codex"]), "today": provider_days["codex"][-1], "sessions": 17},
        ],
        "coverage": {
            "startedAt": to_iso(moment - timedelta(days=90)),
            "notes": [
                "This is demo data. The numbers are invented and describe no real account.",
                "A real snapshot counts tokens from local session logs only. Prompts and replies are never read.",
            ],
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Write the fictional demo data set.")
    parser.add_argument("--out-dir", help="directory to write into (default: public/demo)")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    out_dir = Path(arguments.out_dir).expanduser() if arguments.out_dir else DEMO_DIR
    moment = datetime.now(timezone.utc).replace(microsecond=0)

    inventory = build_inventory(moment)
    write_json_atomic(out_dir / "inventory.json", inventory)
    write_json_atomic(out_dir / "stats.json", build_stats(moment))
    write_json_atomic(out_dir / "characters.json", {"schemaVersion": 1, "characters": {}})
    counts: dict[str, int] = {}
    for item in inventory["items"]:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print("wrote demo data to " + out_dir.name + ": " + str(len(inventory["items"])) + " items " + json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
