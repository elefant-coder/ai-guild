#!/usr/bin/env python3
"""Aggregate local token usage into a privacy-safe statistics snapshot.

Two log formats are understood:

* Codex writes ``~/.codex/sessions/**/*.jsonl`` where each event carries a
  cumulative ``total_token_usage`` counter, so usage is the delta between
  consecutive events.  Resumed sessions inherit the parent's final snapshot as
  their first counter; that inherited delta is subtracted again.
* Claude Code writes ``~/.claude/projects/**/*.jsonl`` where each assistant
  message carries its own usage block.  The same message can appear in several
  files, so messages are deduplicated by a hash of their id and the largest
  counter for a message wins.

Only message ids (hashed), timestamps and token counts are read.  Prompts,
responses, file names inside sessions and project paths are never stored.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from zoneinfo import ZoneInfo

if __package__ in (None, ""):  # executed as a file rather than a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.common import REPO_ROOT, home, load_config, local_machine, now, read_json, truncate, write_json_atomic  # noqa: E402

CACHE_VERSION = 1
MAX_SCAN_BYTES = 256 * 1024 * 1024
STATE_PATH = "~/.local/state/ai-guild/usage/state.json"
METRICS = ("input", "output", "cachedInput", "total")
PROVIDER_NAMES = {"codex": "Codex", "claude": "Claude Code"}
PROVIDER_LOGS = {"codex": ".codex/sessions", "claude": ".claude/projects"}
SUPPORTED_PROVIDERS = tuple(PROVIDER_LOGS)


def zero() -> dict[str, int]:
    """An empty token bucket."""
    return {"input": 0, "output": 0, "cachedInput": 0, "total": 0}


def add(target: dict[str, int], source: dict[str, int]) -> dict[str, int]:
    """Add ``source`` into ``target`` in place."""
    for key in METRICS:
        target[key] = target.get(key, 0) + int(source.get(key, 0))
    return target


def parse_time(value: Any, zone: ZoneInfo) -> datetime | None:
    """Parse a log timestamp into the configured zone."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(zone)


def to_utc(value: Any) -> str | None:
    """Normalise a stored timestamp to an ISO-8601 UTC string."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def codex_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Token bucket for one Codex counter snapshot."""
    used_input = max(0, int(raw.get("input_tokens", 0) or 0))
    used_output = max(0, int(raw.get("output_tokens", 0) or 0))
    cached = min(used_input, max(0, int(raw.get("cached_input_tokens", 0) or 0)))
    return {"input": used_input, "output": used_output, "cachedInput": cached, "total": used_input + used_output}


def claude_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Token bucket for one Claude message, cache reads included as input."""
    uncached = max(0, int(raw.get("input_tokens", 0) or 0))
    cached = max(0, int(raw.get("cache_read_input_tokens", 0) or 0)) + max(0, int(raw.get("cache_creation_input_tokens", 0) or 0))
    used_output = max(0, int(raw.get("output_tokens", 0) or 0))
    return {"input": uncached + cached, "output": used_output, "cachedInput": cached, "total": uncached + cached + used_output}


def add_day(days: dict[str, dict[str, int]], when: datetime | None, amount: dict[str, int]) -> None:
    """Accumulate ``amount`` on the calendar day of ``when``."""
    if when is not None:
        add(days.setdefault(when.date().isoformat(), zero()), amount)


def parse_codex_file(
    path: Path,
    zone: ZoneInfo,
    offset: int = 0,
    limit: int | None = None,
    previous: dict[str, int] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Read one Codex session log from ``offset``, returning its deltas.

    Binary offsets make an oversized log resumable at newline boundaries, so a
    single huge session never blocks the rest of a scan.
    """
    days: dict[str, dict[str, int]] = {}
    counter = dict(previous) if previous else zero()
    first_delta: dict[str, int] | None = None
    first_counter: dict[str, int] | None = None
    first_at: datetime | None = None
    last_at: datetime | None = None
    consumed = 0
    complete = True
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if limit is not None and consumed and consumed + len(line) > limit:
                complete = False
                break
            consumed += len(line)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "session_meta":
                candidate = (event.get("payload") or {}).get("id") if isinstance(event.get("payload"), dict) else None
                if isinstance(candidate, str):
                    session_id = candidate
            payload = event.get("payload")
            info = payload.get("info") if isinstance(payload, dict) else None
            raw = info.get("total_token_usage") if isinstance(info, dict) else None
            when = parse_time(event.get("timestamp"), zone)
            if not isinstance(raw, dict) or when is None:
                continue
            current = codex_usage(raw)
            delta = {key: max(0, current[key] - counter.get(key, 0)) for key in METRICS}
            if first_delta is None:
                first_delta, first_counter, first_at = delta, current, when
            add_day(days, when, delta)
            counter, last_at = current, when
    return {
        "provider": "codex",
        "sessionId": session_id or path.stem,
        "days": days,
        "firstAt": to_utc(first_at.isoformat()) if first_at else None,
        "lastAt": to_utc(last_at.isoformat()) if last_at else None,
        "firstDelta": first_delta or zero(),
        "firstCounter": first_counter or zero(),
        "lastCounter": counter,
        "offset": offset + consumed,
        "complete": complete,
    }


def parse_claude_file(path: Path, zone: ZoneInfo) -> dict[str, Any]:
    """Read one Claude session log, returning per-message usage records."""
    days: dict[str, dict[str, int]] = {}
    messages: list[tuple[str, str, dict[str, int]]] = []
    seen: set[str] = set()
    first_at: datetime | None = None
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            message = message if isinstance(message, dict) else {}
            identifier = message.get("id")
            raw = message.get("usage")
            when = parse_time(event.get("timestamp"), zone)
            if not isinstance(raw, dict) or when is None or not isinstance(identifier, str) or identifier in seen:
                continue
            seen.add(identifier)
            first_at = first_at or when
            amount = claude_usage(raw)
            add_day(days, when, amount)
            # Only an irreversible hash of the message id is kept in the cache.
            messages.append((hashlib.sha256(identifier.encode("utf-8")).hexdigest(), when.date().isoformat(), amount))
    return {
        "provider": "claude",
        "sessionId": path.stem,
        "days": days,
        "firstAt": to_utc(first_at.isoformat()) if first_at else None,
        "messages": messages,
    }


def iter_logs(root: Path, providers: Sequence[str]) -> Iterator[tuple[str, Path]]:
    """Yield (provider, log file) pairs in a stable order."""
    rows: list[tuple[str, Path]] = []
    for provider in providers:
        base = root / PROVIDER_LOGS[provider]
        if base.is_dir():
            rows.extend((provider, path) for path in base.rglob("*.jsonl") if path.is_file())
    for provider, path in sorted(rows, key=lambda row: str(row[1])):
        yield provider, path


def collect(root: Path, state: dict[str, Any], providers: Sequence[str], zone: ZoneInfo) -> int:
    """Scan new or changed log files into ``state``; returns bytes read."""
    files: dict[str, Any] = state.setdefault("files", {})
    seen = state.setdefault("claudeSeen", {})
    if not isinstance(seen, dict):
        seen = {}
    claude_days: dict[str, dict[str, int]] = state.setdefault("claudeDays", {})
    claude_sessions = set(state.setdefault("claudeSessions", []))
    used = 0
    deferred = 0
    provider_used = {provider: 0 for provider in providers}
    provider_cap = MAX_SCAN_BYTES // max(1, len(providers))

    for provider, path in iter_logs(root, providers):
        try:
            status = path.stat()
        except OSError:
            continue
        key = provider + ":" + str(path)
        old = files.get(key)
        identity_changed = bool(old and status.st_ino != old.get("inode"))
        if old and not identity_changed and old.get("size") == status.st_size and old.get("mtime") == status.st_mtime_ns and old.get("complete", True):
            continue
        if provider == "codex" and status.st_size > provider_cap:
            if provider_used[provider]:
                deferred += 1
                continue
            base = None if identity_changed else old
            try:
                chunk = parse_codex_file(
                    path, zone,
                    base.get("offset", 0) if base else 0,
                    provider_cap,
                    base.get("lastCounter") if base else None,
                    base.get("sessionId") if base else None,
                )
            except OSError:
                continue
            if base:
                merged = dict(base)
                merged["days"] = {date: dict(amount) for date, amount in base.get("days", {}).items()}
                for date, amount in chunk["days"].items():
                    add(merged["days"].setdefault(date, zero()), amount)
                for field in ("lastAt", "lastCounter", "offset", "complete"):
                    merged[field] = chunk[field]
                parsed = merged
            else:
                parsed = chunk
            read_bytes = chunk["offset"] - (base.get("offset", 0) if base else 0)
            parsed.update({"size": status.st_size, "mtime": status.st_mtime_ns, "inode": status.st_ino})
            files[key] = parsed
            used += read_bytes
            provider_used[provider] += read_bytes
            continue
        if provider_used[provider] + status.st_size > provider_cap:
            deferred += 1
            continue
        try:
            parsed = parse_codex_file(path, zone) if provider == "codex" else parse_claude_file(path, zone)
        except OSError:
            continue
        if provider == "claude":
            accepted = False
            for digest, date, amount in parsed.pop("messages", []):
                prior = seen.get(digest)
                # A replayed message can gain streamed output later, so the
                # largest complete counter wins rather than the first copy seen.
                if prior and prior["amount"]["total"] >= amount["total"]:
                    continue
                if prior:
                    previous_day = claude_days.setdefault(prior["date"], zero())
                    for metric in METRICS:
                        previous_day[metric] = max(0, previous_day.get(metric, 0) - prior["amount"].get(metric, 0))
                seen[digest] = {"date": date, "amount": amount}
                add(claude_days.setdefault(date, zero()), amount)
                accepted = True
            if accepted:
                claude_sessions.add(hashlib.sha256(str(parsed["sessionId"]).encode("utf-8")).hexdigest())
                first = parsed.get("firstAt")
                if first and (not state.get("claudeFirstAt") or first < state["claudeFirstAt"]):
                    state["claudeFirstAt"] = first
        parsed.update({"size": status.st_size, "mtime": status.st_mtime_ns, "inode": status.st_ino})
        files[key] = parsed
        used += status.st_size
        provider_used[provider] += status.st_size

    state["claudeSeen"] = seen
    state["claudeSessions"] = sorted(claude_sessions)
    state["deferredFiles"] = deferred
    return used


def canonical_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """One record per session: the largest copy of a duplicated log wins."""
    grouped: dict[tuple[Any, Any], tuple[str, dict[str, Any]]] = {}
    for key, record in state.get("files", {}).items():
        if not isinstance(record, dict):
            continue
        session_key = (record.get("provider"), record.get("sessionId") or key)
        current = grouped.get(session_key)
        rank = (record.get("size", 0), record.get("mtime", 0), key)
        if current is None or rank > (current[1].get("size", 0), current[1].get("mtime", 0), current[0]):
            grouped[session_key] = (key, record)
    return [row[1] for row in grouped.values()]


def report(state: dict[str, Any], providers: Sequence[str], zone: ZoneInfo, scope: str, skipped: Sequence[str] = ()) -> dict[str, Any]:
    """Build the statistics snapshot from the accumulated state."""
    records = [record for record in canonical_records(state) if record.get("provider") == "codex" and record.get("firstAt") and record.get("complete", True)]
    days: dict[str, dict[str, int]] = {}
    provider_days: dict[str, dict[str, dict[str, int]]] = {provider: {} for provider in providers}
    if "claude" in provider_days:
        provider_days["claude"] = {date: dict(amount) for date, amount in state.get("claudeDays", {}).items()}
    totals_by_provider = {provider: zero() for provider in providers}
    sessions_by_provider = {provider: 0 for provider in providers}

    terminal = {
        (record.get("lastAt"), tuple(record.get("lastCounter", {}).get(key, 0) for key in METRICS))
        for record in records
    }
    if "codex" in provider_days:
        for record in records:
            sessions_by_provider["codex"] += 1
            # A resumed session starts from its parent's final counter; that
            # inherited jump is not new usage.
            inherited = (
                (record.get("firstAt"), tuple(record.get("firstCounter", {}).get(key, 0) for key in METRICS)) in terminal
                and record.get("firstAt") != record.get("lastAt")
            )
            first_date = ""
            first_at = parse_time(record.get("firstAt"), zone)
            if first_at is not None:
                first_date = first_at.date().isoformat()
            for date, amount in record.get("days", {}).items():
                value = dict(amount)
                if inherited and date == first_date:
                    for key in METRICS:
                        value[key] = max(0, value.get(key, 0) - record.get("firstDelta", {}).get(key, 0))
                add(days.setdefault(date, zero()), value)
                add(provider_days["codex"].setdefault(date, zero()), value)
                add(totals_by_provider["codex"], value)
    if "claude" in provider_days:
        for date, amount in provider_days["claude"].items():
            add(days.setdefault(date, zero()), amount)
            add(totals_by_provider["claude"], amount)
        sessions_by_provider["claude"] = len(state.get("claudeSessions", []))

    today = datetime.now(zone).date()
    dates = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    totals = zero()
    for amount in days.values():
        add(totals, amount)

    rows = []
    for provider in providers:
        totals_row = totals_by_provider[provider]
        if totals_row["total"] == 0 and not sessions_by_provider[provider]:
            continue
        rows.append({
            "id": provider,
            "name": PROVIDER_NAMES.get(provider, provider.title()),
            "totals": totals_row,
            "today": dict(provider_days[provider].get(today.isoformat(), zero())),
            "sessions": sessions_by_provider[provider],
        })
    if not rows and providers:
        rows.append({"id": providers[0], "name": PROVIDER_NAMES.get(providers[0], providers[0].title()), "totals": zero(), "today": zero(), "sessions": 0})

    notes = [
        "Only local session metadata is read: token counts and timestamps. Prompts, replies and file contents are never read.",
        "Codex usage is the difference between consecutive cumulative counters; cachedInput is part of input and is not added twice.",
        "Claude usage counts cache reads and cache writes as input, and deduplicates repeated messages by a hash of the message id.",
    ]
    incomplete = sum(1 for record in state.get("files", {}).values() if isinstance(record, dict) and record.get("provider") == "codex" and not record.get("complete", True))
    if state.get("deferredFiles") or incomplete:
        notes.append("Scan limits left " + str(state.get("deferredFiles", 0)) + " file(s) unread and " + str(incomplete) + " large file(s) partly read. They continue on the next run.")
    if skipped:
        notes.append("These configured providers have no supported local log format and were skipped: " + ", ".join(sorted(skipped)) + ".")
    started = [value for value in [state.get("claudeFirstAt")] + [record.get("firstAt") for record in records] if value]

    return {
        "schemaVersion": 1,
        "collectedAt": now(),
        "scope": truncate(scope, 80) or "This machine",
        "timezone": str(zone),
        "totals": totals,
        "today": dict(days.get(today.isoformat(), zero())),
        "days": [dict({"date": date}, **days.get(date, zero())) for date in dates],
        "providers": rows[:6],
        "coverage": {"startedAt": min(started) if started else None, "notes": [truncate(note, 400) for note in notes][:8]},
    }


def run(root: Path, state_path: Path, providers: Sequence[str], zone: ZoneInfo, scope: str, skipped: Sequence[str] = ()) -> dict[str, Any]:
    """Refresh the cache under a lock and return a statistics snapshot."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            snapshot = read_json(state_path, {})
            return report(snapshot if isinstance(snapshot, dict) else {}, providers, zone, scope, skipped)
        state = read_json(state_path, {})
        if not isinstance(state, dict) or state.get("version") != CACHE_VERSION or state.get("timezone") != str(zone):
            state = {"version": CACHE_VERSION, "timezone": str(zone), "files": {}}
        collect(root, state, providers, zone)
        write_json_atomic(state_path, state, mode=0o600)
        return report(state, providers, zone, scope, skipped)


def snapshot(config: dict[str, Any], root: Path | None = None, state_path: Path | None = None) -> dict[str, Any]:
    """Build a snapshot for one configuration, reading the local log folders."""
    usage = config.get("collector", {}).get("usage", {})
    requested = usage.get("providers") if isinstance(usage, dict) else None
    requested = [str(entry) for entry in requested] if isinstance(requested, list) and requested else list(SUPPORTED_PROVIDERS)
    providers = [provider for provider in requested if provider in SUPPORTED_PROVIDERS][:6] or list(SUPPORTED_PROVIDERS)
    skipped = [provider for provider in requested if provider not in SUPPORTED_PROVIDERS]
    try:
        zone = ZoneInfo(str(config.get("timezone", "UTC")))
    except Exception:
        zone = ZoneInfo("UTC")
    scope = str(local_machine(config).get("label", "This machine"))
    return run(
        root or home(),
        state_path or Path(os.path.expanduser(STATE_PATH)),
        providers,
        zone,
        scope,
        skipped,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Aggregate local token usage into a statistics snapshot.")
    parser.add_argument("--out", help="write the snapshot here (default: data/stats.json)")
    parser.add_argument("--stdout", action="store_true", help="print the snapshot instead of writing a file")
    parser.add_argument("--root", help="directory that holds the CLI log folders (default: home)")
    parser.add_argument("--state", help="scan cache location")
    parser.add_argument("--config", help="path to a guild configuration file")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    config = load_config(arguments.config)
    root = Path(arguments.root).expanduser() if arguments.root else None
    state_path = Path(arguments.state).expanduser() if arguments.state else None
    result = snapshot(config, root, state_path)

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.stdout:
        sys.stdout.write(rendered)
        return 0
    out_path = Path(arguments.out).expanduser() if arguments.out else REPO_ROOT / "data" / "stats.json"
    write_json_atomic(out_path, result)
    print("wrote " + out_path.name + ": " + str(result["totals"]["total"]) + " tokens across " + str(len(result["providers"])) + " provider(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
