#!/usr/bin/env python3
"""Validate a collector document before it is published.

Checks an inventory (or a full upload envelope) against
``docs/INVENTORY-SCHEMA.md``, and a statistics snapshot with ``--stats``.  The
same rules run again on the server, so a document that passes here is the one
the server will accept.  Every check is local: nothing is uploaded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__ in (None, ""):  # executed as a file rather than a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.common import CATEGORY_ORDER, SCHEDULE_KINDS, STATUSES  # noqa: E402

MAX_ITEMS = 2_500
MAX_SKEW_SECONDS = 300

INVENTORY_KEYS = {"collectedAt", "items", "sources", "machines", "notes"}
ENVELOPE_KEYS = {"schemaVersion", "source", "revision", "collectedAt", "heartbeatAt", "inventory"}
ITEM_KEYS = {"id", "name", "category", "summary", "tags", "status", "statusLabel", "machines", "runtimes", "evidence", "relatedIds", "command", "schedule", "meta"}
EVIDENCE_KEYS = {"source", "detail", "checkedAt"}
SCHEDULE_KEYS = {"kind", "label", "intervalSeconds", "calendar", "loaded", "running", "lastExitCode"}
SOURCE_KEYS = {"name", "collectedAt", "count", "scope"}
MACHINE_KEYS = {"id", "status", "statusLabel", "checkedAt", "architecture", "os", "jobCount", "loadedJobCount", "runningJobCount", "skillCount", "runtimes", "reachability", "stale", "lastAttemptAt", "lastSuccessfulAt"}
META_KEYS = {
    "title", "strengths", "exampleTask", "role", "origin", "modelPolicy", "variants",
    "descriptionOriginal", "descriptionOriginalVariants", "sources", "sourceCount", "sourceFile",
    "label", "runAtLoad", "event", "hookGroups", "provider", "model", "effort", "contextWindow",
    "defaultReasoning", "executable", "enabled", "toolCount", "summarySource", "sessionAvailability",
    "visibleToCli", "rosterScope", "schedulerId",
}
MACHINE_STATUSES = {"observed", "configured", "attention", "unknown", "unavailable"}

ITEM_ID = re.compile(r"^[A-Za-z0-9@-]{1,160}$")
MACHINE_ID = re.compile(r"^[a-z0-9-]{1,32}$")
DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$")
FORBIDDEN_KEY = re.compile(r"(?:password|secret|api.?key|access.?token|refresh.?token|authorization|credential|environment|^env$|^token$|arguments|^args$|^payload$|^message$|^body$)", re.IGNORECASE)

SECRET_PATTERNS = (
    ("OpenAI-style key", re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{15,}")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{25,}")),
    ("bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE)),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("absolute home path", re.compile(r"/Users/")),
    ("absolute home path", re.compile(r"/home/")),
)


class Report:
    """Collects problems so every failure is reported in one pass."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.notes: list[str] = []

    def check(self, condition: Any, message: str) -> bool:
        """Record ``message`` when ``condition`` is falsy."""
        if not condition:
            self.problems.append(message)
            return False
        return True

    def note(self, message: str) -> None:
        """Record an informational remark that does not fail validation."""
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return not self.problems


def is_iso(value: Any, *, allow_future: bool = False) -> bool:
    """True for an ISO-8601 UTC timestamp that is not implausibly future."""
    if not isinstance(value, str) or not ISO.match(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if allow_future:
        return True
    return parsed <= datetime.now(timezone.utc) + timedelta(seconds=MAX_SKEW_SECONDS)


def is_text(value: Any, limit: int, *, allow_empty: bool = False) -> bool:
    """True for a string within ``limit`` characters."""
    return isinstance(value, str) and (allow_empty or bool(value)) and len(value) <= limit


def is_count(value: Any) -> bool:
    """True for a non-negative integer (booleans excluded)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def only_keys(value: Any, allowed: Iterable[str]) -> bool:
    """True for a plain object whose keys are all allowed and not secret-ish."""
    allowed = set(allowed)
    return (
        isinstance(value, dict)
        and all(key in allowed and not FORBIDDEN_KEY.search(key) for key in value)
    )


def safe_value(value: Any, depth: int = 0) -> bool:
    """True for bounded JSON data with no secret-looking keys."""
    if depth > 4:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 4_000
    if isinstance(value, list):
        return len(value) <= 200 and all(safe_value(entry, depth + 1) for entry in value)
    if isinstance(value, dict):
        return len(value) <= 80 and all(not FORBIDDEN_KEY.search(key) and safe_value(entry, depth + 1) for key, entry in value.items())
    return False


def validate_item(item: Any, machine_ids: set[str], index: int, report: Report) -> str | None:
    """Validate one inventory item; returns its id when it has a usable one."""
    label = "items[" + str(index) + "]"
    if not report.check(only_keys(item, ITEM_KEYS), label + ": unexpected keys"):
        return None
    identifier = item.get("id")
    if not report.check(isinstance(identifier, str) and bool(ITEM_ID.match(identifier)), label + ": id must match ^[A-Za-z0-9@-]{1,160}$"):
        return None
    label = identifier
    report.check(is_text(item.get("name"), 400), label + ": name must be 1..400 characters")
    report.check(is_text(item.get("summary"), 4_000), label + ": summary must be 1..4000 characters")
    report.check(item.get("category") in CATEGORY_ORDER, label + ": unknown category")
    report.check(item.get("status") in STATUSES, label + ": unknown status")
    report.check(is_text(item.get("statusLabel"), 500), label + ": statusLabel must be 1..500 characters")

    tags = item.get("tags")
    report.check(isinstance(tags, list) and len(tags) <= 50 and all(is_text(tag, 120) for tag in tags), label + ": tags must be up to 50 strings of 120 characters")
    runtimes = item.get("runtimes")
    report.check(isinstance(runtimes, list) and all(is_text(runtime, 120) for runtime in runtimes), label + ": runtimes must be strings of up to 120 characters")

    machines = item.get("machines")
    if report.check(isinstance(machines, list) and bool(machines), label + ": machines must list at least one machine"):
        for machine in machines:
            report.check(isinstance(machine, str) and machine in machine_ids, label + ": machine '" + str(machine) + "' is not declared in machines")

    evidence = item.get("evidence")
    if report.check(isinstance(evidence, list) and 1 <= len(evidence) <= 50, label + ": evidence must hold 1..50 records"):
        for record in evidence:
            ok = only_keys(record, EVIDENCE_KEYS) and is_text(record.get("source"), 500) and is_text(record.get("detail"), 1_000) and is_iso(record.get("checkedAt"))
            report.check(ok, label + ": evidence record must be {source, detail, checkedAt}")

    if "command" in item:
        report.check(is_text(item["command"], 1_000), label + ": command must be 1..1000 characters")

    schedule = item.get("schedule")
    if schedule is not None:
        if report.check(only_keys(schedule, SCHEDULE_KEYS), label + ": schedule has unexpected keys"):
            report.check(schedule.get("kind") in SCHEDULE_KINDS, label + ": unknown schedule kind")
            report.check(is_text(schedule.get("label"), 300), label + ": schedule label must be 1..300 characters")
            if "intervalSeconds" in schedule:
                report.check(is_count(schedule["intervalSeconds"]), label + ": intervalSeconds must be a non-negative integer")
            if "calendar" in schedule:
                report.check(safe_value(schedule["calendar"]), label + ": calendar payload is not safe to publish")
        report.check(item.get("category") == "automation", label + ": only automation items may carry a schedule")

    meta = item.get("meta")
    if meta is not None:
        if report.check(only_keys(meta, META_KEYS), label + ": meta has keys outside the allowed list"):
            report.check(all(safe_value(value) for value in meta.values()), label + ": meta values are not safe to publish")

    if item.get("category") == "agent":
        strengths = (meta or {}).get("strengths")
        report.check(isinstance(strengths, list) and bool(strengths) and all(is_text(entry, 200) for entry in strengths), label + ": agent items need meta.strengths")
        report.check(is_text((meta or {}).get("exampleTask"), 300), label + ": agent items need meta.exampleTask")

    if identifier.startswith("automation-private-"):
        report.check("label" not in (meta or {}) and "sourceFile" not in (meta or {}), label + ": private jobs must not publish meta.label or meta.sourceFile")
    return identifier


def validate_inventory(inventory: Any, report: Report) -> dict[str, int]:
    """Validate an inventory document and return its per-category counts."""
    counts: dict[str, int] = {category: 0 for category in CATEGORY_ORDER}
    if not report.check(only_keys(inventory, INVENTORY_KEYS), "inventory: unexpected top-level keys"):
        return counts
    report.check(is_iso(inventory.get("collectedAt")), "inventory.collectedAt must be an ISO-8601 UTC timestamp that is not in the future")

    machines = inventory.get("machines")
    machine_ids: set[str] = set()
    if report.check(isinstance(machines, dict) and bool(machines), "inventory.machines must be a non-empty object"):
        for machine_id, record in machines.items():
            report.check(bool(MACHINE_ID.match(str(machine_id))), "machines." + str(machine_id) + ": id must match ^[a-z0-9-]{1,32}$")
            machine_ids.add(str(machine_id))
            if report.check(only_keys(record, MACHINE_KEYS), "machines." + str(machine_id) + ": unexpected keys"):
                report.check(all(safe_value(value) for value in record.values()), "machines." + str(machine_id) + ": values are not safe to publish")
                if "status" in record:
                    report.check(record["status"] in MACHINE_STATUSES, "machines." + str(machine_id) + ": unknown status")
                if "checkedAt" in record:
                    report.check(is_iso(record["checkedAt"]), "machines." + str(machine_id) + ": checkedAt must be an ISO-8601 UTC timestamp")

    items = inventory.get("items")
    if not report.check(isinstance(items, list), "inventory.items must be a list"):
        return counts
    report.check(len(items) <= MAX_ITEMS, "inventory.items must hold at most " + str(MAX_ITEMS) + " entries")

    identifiers: set[str] = set()
    for index, item in enumerate(items):
        identifier = validate_item(item, machine_ids, index, report)
        if identifier is None:
            continue
        report.check(identifier not in identifiers, "duplicate item id: " + identifier)
        identifiers.add(identifier)
        category = item.get("category")
        if category in counts:
            counts[category] += 1
    for item in items:
        if not isinstance(item, dict):
            continue
        for related in item.get("relatedIds", []) if isinstance(item.get("relatedIds"), list) else []:
            report.check(related in identifiers, str(item.get("id")) + ": relatedIds points at a missing item: " + str(related))

    sources = inventory.get("sources")
    if report.check(isinstance(sources, list) and len(sources) <= 20, "inventory.sources must be a list of at most 20 entries"):
        total = 0
        for source in sources:
            if report.check(only_keys(source, SOURCE_KEYS), "sources: unexpected keys"):
                report.check(is_text(source.get("name"), 200), "sources: name must be 1..200 characters")
                report.check(is_iso(source.get("collectedAt")), "sources: collectedAt must be an ISO-8601 UTC timestamp")
                report.check(is_count(source.get("count")), "sources: count must be a non-negative integer")
                if "scope" in source:
                    report.check(is_text(source["scope"], 1_000), "sources: scope must be 1..1000 characters")
                total += source.get("count") if is_count(source.get("count")) else 0
        report.check(total == len(items), "source counts (" + str(total) + ") must add up to the item count (" + str(len(items)) + ")")

    notes = inventory.get("notes")
    report.check(isinstance(notes, list) and len(notes) <= 100 and all(is_text(note, 2_000) for note in notes), "inventory.notes must be up to 100 strings of 2000 characters")

    missing = [category for category, count in counts.items() if not count]
    if missing:
        report.note("no items in these categories: " + ", ".join(missing))
    return counts


def validate_envelope(payload: dict[str, Any], report: Report) -> None:
    """Validate the upload envelope wrapped around an inventory."""
    report.check(only_keys(payload, ENVELOPE_KEYS), "envelope: unexpected keys")
    report.check(payload.get("schemaVersion") == 1, "envelope.schemaVersion must be 1")
    report.check(payload.get("source") == "collector", "envelope.source must be the literal 'collector'")
    revision = payload.get("revision")
    report.check(isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1, "envelope.revision must be an integer of at least 1")
    report.check(is_iso(payload.get("collectedAt")), "envelope.collectedAt must be an ISO-8601 UTC timestamp")
    report.check(is_iso(payload.get("heartbeatAt")), "envelope.heartbeatAt must be an ISO-8601 UTC timestamp")
    if is_iso(payload.get("collectedAt")) and is_iso(payload.get("heartbeatAt")):
        report.check(payload["heartbeatAt"] >= payload["collectedAt"], "envelope.heartbeatAt must not precede collectedAt")


def metrics(value: Any) -> bool:
    """True for a token bucket that satisfies the arithmetic in the schema."""
    if not only_keys(value, {"input", "output", "cachedInput", "total"}) or len(value) != 4:
        return False
    if not all(is_count(value.get(key)) for key in ("input", "output", "cachedInput", "total")):
        return False
    return value["cachedInput"] <= value["input"] and value["total"] == value["input"] + value["output"]


def validate_stats(payload: Any, report: Report) -> dict[str, Any]:
    """Validate a statistics snapshot and return a short summary."""
    summary: dict[str, Any] = {"providers": 0, "days": 0, "total": 0}
    if not report.check(only_keys(payload, {"schemaVersion", "collectedAt", "scope", "timezone", "totals", "today", "days", "providers", "coverage"}), "stats: unexpected keys"):
        return summary
    report.check(payload.get("schemaVersion") == 1, "stats.schemaVersion must be 1")
    report.check(is_text(payload.get("scope"), 80), "stats.scope must be 1..80 characters")
    report.check(is_iso(payload.get("collectedAt")), "stats.collectedAt must be an ISO-8601 UTC timestamp that is not in the future")

    zone = None
    timezone_name = payload.get("timezone")
    if report.check(is_text(timezone_name, 64), "stats.timezone must be an IANA time zone name"):
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            report.check(False, "stats.timezone is not a known IANA time zone: " + str(timezone_name))

    report.check(metrics(payload.get("totals")), "stats.totals must be a valid token bucket")
    report.check(metrics(payload.get("today")), "stats.today must be a valid token bucket")

    days = payload.get("days")
    if report.check(isinstance(days, list) and len(days) == 7, "stats.days must hold exactly 7 entries"):
        summary["days"] = len(days)
        previous: datetime | None = None
        for day in days:
            if not report.check(only_keys(day, {"date", "input", "output", "cachedInput", "total"}), "stats.days: unexpected keys"):
                continue
            if not report.check(DATE.match(str(day.get("date"))) is not None, "stats.days: date must be YYYY-MM-DD"):
                continue
            bucket = {key: value for key, value in day.items() if key != "date"}
            report.check(metrics(bucket), "stats.days[" + str(day["date"]) + "] is not a valid token bucket")
            current = datetime.strptime(day["date"], "%Y-%m-%d")
            if previous is not None:
                report.check(current - previous == timedelta(days=1), "stats.days must be seven consecutive days")
            previous = current
        if zone is not None and is_iso(payload.get("collectedAt")):
            collected = datetime.fromisoformat(payload["collectedAt"].replace("Z", "+00:00")).astimezone(zone)
            report.check(days[-1].get("date") == collected.date().isoformat(), "stats.days must end on today in " + str(timezone_name))
        if metrics(payload.get("today")) and only_keys(days[-1], {"date", "input", "output", "cachedInput", "total"}):
            same = all(payload["today"].get(key) == days[-1].get(key) for key in ("input", "output", "cachedInput", "total"))
            report.check(same, "stats.today must equal the last day in stats.days")
        if metrics(payload.get("totals")) and metrics(payload.get("today")):
            report.check(all(payload["today"][key] <= payload["totals"][key] for key in payload["today"]), "stats.today must not exceed stats.totals")

    providers = payload.get("providers")
    if report.check(isinstance(providers, list) and 1 <= len(providers) <= 6, "stats.providers must hold 1..6 entries"):
        summary["providers"] = len(providers)
        identifiers = []
        for provider in providers:
            if not report.check(only_keys(provider, {"id", "name", "totals", "today", "sessions"}), "stats.providers: unexpected keys"):
                continue
            report.check(is_text(provider.get("id"), 40), "stats.providers: id must be 1..40 characters")
            report.check(is_text(provider.get("name"), 80), "stats.providers: name must be 1..80 characters")
            report.check(metrics(provider.get("totals")), "stats.providers[" + str(provider.get("id")) + "].totals is not a valid token bucket")
            report.check(metrics(provider.get("today")), "stats.providers[" + str(provider.get("id")) + "].today is not a valid token bucket")
            report.check(is_count(provider.get("sessions")), "stats.providers: sessions must be a non-negative integer")
            identifiers.append(provider.get("id"))
        report.check(len(set(identifiers)) == len(identifiers), "stats.providers must have unique ids")
        if all(metrics(provider.get("totals")) and metrics(provider.get("today")) for provider in providers) and metrics(payload.get("totals")) and metrics(payload.get("today")):
            for key in ("input", "output", "cachedInput", "total"):
                report.check(sum(provider["totals"][key] for provider in providers) == payload["totals"][key], "provider totals must add up to stats.totals (" + key + ")")
                report.check(sum(provider["today"][key] for provider in providers) == payload["today"][key], "provider today values must add up to stats.today (" + key + ")")

    coverage = payload.get("coverage")
    if report.check(only_keys(coverage, {"startedAt", "notes"}), "stats.coverage must be {startedAt, notes}"):
        started = coverage.get("startedAt")
        report.check(started is None or is_iso(started), "stats.coverage.startedAt must be null or an ISO-8601 UTC timestamp")
        notes = coverage.get("notes")
        ok = isinstance(notes, list) and len(notes) <= 8 and all(is_text(note, 400) and not re.search(r"[\x00-\x1f]", note) for note in notes)
        report.check(ok, "stats.coverage.notes must be up to 8 plain strings of 400 characters")
    if metrics(payload.get("totals")):
        summary["total"] = payload["totals"]["total"]
    return summary


def scan_secrets(raw: str, report: Report) -> None:
    """Reject any document that carries a secret-looking string."""
    for name, pattern in SECRET_PATTERNS:
        match = pattern.search(raw)
        if match:
            report.check(False, "secret-looking content rejected (" + name + ") near: " + raw[max(0, match.start() - 20):match.start() + 30].replace("\n", " "))


def validate_file(path: Path, as_stats: bool) -> tuple[bool, str]:
    """Validate one file and return (ok, message)."""
    report = Report()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        reason = error.strerror or "unreadable"
        return False, "FAIL " + path.name + ": cannot read the file (" + reason + ")"
    try:
        payload = json.loads(raw)
    except ValueError as error:
        return False, path.name + " is not valid JSON: " + str(error)
    scan_secrets(raw, report)
    if as_stats:
        summary = validate_stats(payload, report)
        headline = "PASS " + path.name + ": " + str(summary["days"]) + " days, " + str(summary["providers"]) + " provider(s), " + str(summary["total"]) + " tokens total."
    else:
        if isinstance(payload, dict) and isinstance(payload.get("inventory"), dict):
            validate_envelope(payload, report)
            inventory = payload["inventory"]
        else:
            inventory = payload
        counts = validate_inventory(inventory, report)
        total = sum(counts.values())
        detail = ", ".join(category + "=" + str(counts[category]) for category in CATEGORY_ORDER)
        machines = len(inventory.get("machines", {})) if isinstance(inventory, dict) and isinstance(inventory.get("machines"), dict) else 0
        headline = "PASS " + path.name + ": " + str(total) + " items (" + detail + ") across " + str(machines) + " machine(s)."
    if not report.ok:
        lines = ["FAIL " + path.name + ": " + str(len(report.problems)) + " problem(s)"]
        lines.extend("  - " + problem for problem in report.problems[:50])
        if len(report.problems) > 50:
            lines.append("  - ... and " + str(len(report.problems) - 50) + " more")
        return False, "\n".join(lines)
    for note in report.notes:
        headline += "\n  note: " + note
    return True, headline


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Validate collector output against the published schema.")
    parser.add_argument("files", nargs="*", help="inventory JSON files to validate")
    parser.add_argument("--stats", action="append", default=[], help="statistics JSON file to validate")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if not arguments.files and not arguments.stats:
        parser.error("give at least one inventory file or --stats file")

    failed = False
    for name in arguments.files:
        ok, message = validate_file(Path(name).expanduser(), as_stats=False)
        print(message)
        failed = failed or not ok
    for name in arguments.stats:
        ok, message = validate_file(Path(name).expanduser(), as_stats=True)
        print(message)
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
