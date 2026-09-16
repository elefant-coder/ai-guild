#!/usr/bin/env python3
"""Refresh the local inventory and publish it to the guild site.

The loop is deliberately cheap: it fingerprints watched files by name, size and
modification time, re-runs only the scan modules whose inputs changed, and then
either uploads a new revision or sends a heartbeat that carries nothing but
freshness metadata.  Request headers and bodies are never written to the log.

Configuration lives outside the repository in ``~/.config/ai-guild/sync.json``::

    {"endpoint": "https://<worker>/api/inventory",
     "ingestSecretFile": "~/.config/ai-guild/ingest.secret"}
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):  # executed as a file rather than a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import scan, usage  # noqa: E402
from collector.common import REPO_ROOT, home, load_config, local_machine, now, read_json, remote_machines, write_json_atomic  # noqa: E402

SYNC_CONFIG = "~/.config/ai-guild/sync.json"
STATE_FILE = "~/.local/state/ai-guild/sync-state.json"
USAGE_STATE = "~/.local/state/ai-guild/usage/state.json"
LOG_FILE = "~/.local/state/ai-guild/sync.log"
LOG_LIMIT_BYTES = 256 * 1024
LOOP_SECONDS = 30
RUNTIME_PROBE_SECONDS = 60
AUTOMATION_PROBE_SECONDS = 60
REMOTE_PROBE_SECONDS = 120
STATS_PROBE_SECONDS = 60
REQUEST_TIMEOUT = 20
MAX_BACKOFF_SECONDS = 900
VOLATILE_KEYS = {"collectedAt", "checkedAt", "generatedAt", "heartbeatAt", "receivedAt", "ageSeconds"}
REMOTE_STATE_KEYS = {
    "revision", "serverContentHash", "inventoryDigest", "pendingEnvelope", "pendingHeartbeat",
    "fullRebasedOnce", "retryAfter", "retryCount", "lastSuccessAt", "lastHeartbeatAt",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so a credential can never follow one elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib contract
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def post(url: str, body: bytes, secret: str, timeout: int = REQUEST_TIMEOUT, content_type: str = "application/json", extra_headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """POST a body and return (status, parsed reply); 0 means no response."""
    headers = {
        "Authorization": "Bearer " + secret,
        "Content-Type": content_type,
        "User-Agent": "AIGuildCollector/1.0",
    }
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read(8_192)
            status = getattr(response, "status", response.getcode())
    except urllib.error.HTTPError as error:
        raw = error.read(8_192)
        status = error.code
        error.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def fingerprint(paths: Sequence[Path]) -> str:
    """Hash file names, sizes and modification times; never file contents."""
    rows: list[str] = []
    for root in paths:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file() and not path.name.startswith(".")]
        for path in candidates:
            try:
                status = path.stat()
            except OSError:
                continue
            rows.append(str(path) + ":" + str(status.st_mtime_ns) + ":" + str(status.st_size))
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def semantic_digest(value: Any) -> str:
    """Canonical hash of an inventory with freshness fields removed."""

    def strip(current: Any) -> Any:
        if isinstance(current, dict):
            return {key: strip(entry) for key, entry in current.items() if key not in VOLATILE_KEYS}
        if isinstance(current, list):
            return [strip(entry) for entry in current]
        return current

    return hashlib.sha256(json.dumps(strip(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class Runtime:
    """Paths, configuration and mutable state for one sync process."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        self.config = load_config(arguments.config)
        self.machine = str(local_machine(self.config).get("id", "main"))
        self.machine_label = str(local_machine(self.config).get("label", ""))
        self.data_dir = Path(arguments.data_dir).expanduser() if arguments.data_dir else REPO_ROOT / "data"
        self.parts_dir = self.data_dir / "parts"
        self.inventory_path = self.data_dir / "inventory.json"
        self.stats_path = self.data_dir / "stats.json"
        self.sync_config_path = Path(arguments.sync_config or os.path.expanduser(SYNC_CONFIG)).expanduser()
        self.state_path = Path(arguments.state or os.path.expanduser(STATE_FILE)).expanduser()
        self.usage_state_path = Path(arguments.usage_state or os.path.expanduser(USAGE_STATE)).expanduser()
        self.log_path = Path(arguments.log or os.path.expanduser(LOG_FILE)).expanduser()
        self.allow_http = bool(arguments.allow_http)
        self.usage_root = Path(arguments.usage_root).expanduser() if arguments.usage_root else home()
        self.state: dict[str, Any] = {}

    def log(self, message: str) -> None:
        """Append one bounded status line; rotates at 256 KiB."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_LIMIT_BYTES:
                os.replace(self.log_path, self.log_path.with_suffix(".log.1"))
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(now() + " " + message + "\n")
        except OSError:
            pass


def watched_paths(runtime: Runtime) -> dict[str, list[Path]]:
    """Files and folders whose changes should trigger each scan module."""
    roots = runtime.config.get("collector", {}).get("roots", {})
    roots = roots if isinstance(roots, dict) else {}
    claude = scan.expand(str(roots.get("claude", "~/.claude")))
    codex = scan.expand(str(roots.get("codex", "~/.codex")))
    shared = scan.expand(str(roots.get("sharedSkills", "~/.agents/skills")))
    return {
        "runtime": [
            codex / "config.toml", codex / "models_cache.json", claude / "settings.json",
            home() / ".claude.json", claude / "mcp-needs-auth-cache.json",
            home() / ".gemini/settings.json", home() / ".config/goose/config.yaml", home() / ".grok/config.toml",
        ],
        "skills": [shared, codex / "skills", codex / "plugins" / "cache", claude / "skills", claude / "plugins" / "cache"],
        "agents": [claude / "agents", codex / "agents", claude / "plugins" / "cache"],
        "harness": [claude / "settings.json", claude / "rules", claude / "CLAUDE.md", codex / "AGENTS.md"],
    }


def refresh(runtime: Runtime, force: bool = False) -> bool:
    """Re-run the scan modules whose inputs changed and rebuild the inventory."""
    state = runtime.state
    clock = time.monotonic()
    watched = watched_paths(runtime)
    context = scan.Context(runtime.config, runtime.machine)
    changed = False

    due: list[str] = []
    for module in ("runtime", "skills", "agents", "harness"):
        digest = fingerprint(watched[module])
        stale = digest != state.get(module + "Fingerprint")
        timed_out = module == "runtime" and clock - state.get("runtimeProbeMonotonic", 0) >= RUNTIME_PROBE_SECONDS
        missing = not (runtime.parts_dir / (module + ".json")).is_file()
        if force or stale or timed_out or missing:
            due.append(module)
            state[module + "Fingerprint"] = digest
            if module == "runtime":
                state["runtimeProbeMonotonic"] = clock
    if force or clock - state.get("automationProbeMonotonic", 0) >= AUTOMATION_PROBE_SECONDS or not (runtime.parts_dir / "automation.json").is_file():
        due.append("automation")
        state["automationProbeMonotonic"] = clock

    for module in scan.MODULES:
        if module in due:
            part = scan.collect_module(context, module)
            write_json_atomic(runtime.parts_dir / (module + ".json"), part)
            changed = True

    parts: dict[str, dict[str, Any]] = {}
    for module in scan.MODULES:
        part = read_json(runtime.parts_dir / (module + ".json"), None)
        if isinstance(part, dict) and isinstance(part.get("items"), list):
            parts[module] = part
    if not parts:
        return False

    remote_records: dict[str, dict[str, Any]] = state.get("remoteMachines", {}) if isinstance(state.get("remoteMachines"), dict) else {}
    remote_items: dict[str, list[dict[str, Any]]] = state.get("remoteItems", {}) if isinstance(state.get("remoteItems"), dict) else {}
    machines = remote_machines(runtime.config)
    if machines and (force or clock - state.get("remoteProbeMonotonic", 0) >= REMOTE_PROBE_SECONDS):
        state["remoteProbeMonotonic"] = clock
        previous = read_json(runtime.inventory_path, {}) or {}
        source = scan.read_text(Path(scan.__file__), 1_000_000)
        for machine in machines:
            machine_id = str(machine["id"])
            payload = scan.remote_scan(machine, source) if source else None
            if payload is None:
                remote_records[machine_id] = scan.unavailable_machine(machine, previous.get("machines", {}).get(machine_id), context.checked_at)
                continue
            remote_items[machine_id] = [item for item in payload.get("items", []) if isinstance(item, dict)]
            record = payload.get("machines", {}).get(machine_id)
            remote_records[machine_id] = record if isinstance(record, dict) else {"id": machine_id, "status": "unknown", "checkedAt": context.checked_at}
            changed = True
        state["remoteMachines"] = remote_records
        state["remoteItems"] = remote_items

    label = "Scanned locally, read only: " + runtime.machine_label if runtime.machine_label else ""
    inventory = scan.build_inventory(context, parts, remote_records, label, remote_items)
    write_json_atomic(runtime.inventory_path, inventory)
    state["heartbeatAt"] = now()
    return changed


def sync_settings(runtime: Runtime) -> tuple[dict[str, Any], str]:
    """Validate the sync configuration and read the ingest credential."""
    raw = read_json(runtime.sync_config_path, {})
    if not isinstance(raw, dict) or not raw.get("endpoint") or not raw.get("ingestSecretFile"):
        return {}, "sync configuration is missing"
    parsed = urllib.parse.urlparse(str(raw["endpoint"]))
    if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params:
        return {}, "sync endpoint is not a plain https URL"
    if parsed.path != "/api/inventory":
        return {}, "sync endpoint must end in /api/inventory"
    if parsed.scheme != "https" and not runtime.allow_http:
        return {}, "sync endpoint must use https"
    secret_path = Path(str(raw["ingestSecretFile"])).expanduser()
    try:
        if secret_path.stat().st_mode & 0o077:
            return {}, "ingest secret file must not be readable by others (chmod 600)"
        secret = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}, "ingest secret file cannot be read"
    if not secret:
        return {}, "ingest secret file is empty"
    return {
        "endpoint": urllib.parse.urlunparse(parsed),
        "heartbeat": urllib.parse.urlunparse(parsed._replace(path="/api/inventory/heartbeat")),
        "stats": urllib.parse.urlunparse(parsed._replace(path="/api/stats")),
        "secret": secret,
    }, ""


def reset_remote_state(state: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Forget remote claims when the destination changes; keep local timing."""
    identity = hashlib.sha256(str(settings["endpoint"]).encode("utf-8")).hexdigest()
    if state.get("remoteIdentity") == identity:
        return False
    for key in REMOTE_STATE_KEYS:
        state.pop(key, None)
    state["remoteIdentity"] = identity
    state["syncStatus"] = "pending-newer"
    state["syncDetail"] = ""
    return True


def backoff(state: dict[str, Any]) -> None:
    """Grow the retry delay up to fifteen minutes."""
    state["retryCount"] = int(state.get("retryCount", 0)) + 1
    state["retryAfter"] = time.time() + min(MAX_BACKOFF_SECONDS, 30 * 2 ** min(5, state["retryCount"] - 1))


def publish(runtime: Runtime) -> bool:
    """Upload a new inventory revision, retrying the same body on failure."""
    state = runtime.state
    settings, error = sync_settings(runtime)
    if error:
        state["syncStatus"], state["syncDetail"] = "blocked", error
        return False
    inventory = read_json(runtime.inventory_path, None)
    if not isinstance(inventory, dict):
        state["syncStatus"], state["syncDetail"] = "blocked", "inventory has not been built yet"
        return False
    digest = semantic_digest(inventory)
    if digest == state.get("inventoryDigest") and not state.get("pendingEnvelope") and state.get("syncStatus") != "pending-newer":
        state["syncStatus"] = "synced"
        return True

    pending = state.get("pendingEnvelope")
    if isinstance(pending, dict):
        envelope = pending["body"]
        revision = int(envelope["revision"])
        accepted_digest = str(pending["inventoryDigest"])
    else:
        revision = int(state.get("revision", 0)) + 1
        envelope = {
            "schemaVersion": 1,
            "source": "collector",
            "revision": revision,
            "collectedAt": inventory.get("collectedAt"),
            "heartbeatAt": state.get("heartbeatAt") or now(),
            "inventory": inventory,
        }
        state["pendingEnvelope"] = {"inventoryDigest": digest, "body": envelope}
        accepted_digest = digest

    status, reply = post(str(settings["endpoint"]), json.dumps(envelope, ensure_ascii=False).encode("utf-8"), str(settings["secret"]))
    if status in (200, 201):
        sync = reply.get("sync")
        if not isinstance(sync, dict) or reply.get("accepted") is not True or int(sync.get("revision", -1)) != revision or not isinstance(sync.get("contentHash"), str):
            state["syncStatus"], state["syncDetail"] = "blocked", "the server reply could not be verified"
            return False
        state.update({
            "revision": revision,
            "serverContentHash": sync["contentHash"],
            "inventoryDigest": accepted_digest,
            "lastSuccessAt": now(),
            "syncStatus": "pending-newer" if accepted_digest != digest else "synced",
            "syncDetail": "",
            "retryCount": 0,
            "retryAfter": 0,
        })
        for key in ("pendingEnvelope", "pendingHeartbeat", "fullRebasedOnce"):
            state.pop(key, None)
        return True
    if status == 409 and not state.get("fullRebasedOnce"):
        current = reply.get("currentRevision")
        if isinstance(current, int) and current >= 1:
            state["revision"] = max(int(state.get("revision", 0)), current)
            state["fullRebasedOnce"] = True
            state["forceRefresh"] = True
            state.pop("pendingEnvelope", None)
            state.pop("pendingHeartbeat", None)
            state["syncStatus"], state["syncDetail"] = "pending-newer", "rebased on the revision the server reported"
            return False
    if status == 0 or status >= 500:
        state["syncStatus"], state["syncDetail"] = "retry", "no usable response" if status == 0 else "server error"
        backoff(state)
        return False
    state["syncStatus"] = "blocked"
    state["syncDetail"] = "the server rejected the upload" if status not in (401, 403) else "the ingest credential was rejected"
    return False


def heartbeat(runtime: Runtime) -> bool:
    """Tell the server the current revision is still fresh."""
    state = runtime.state
    settings, error = sync_settings(runtime)
    if error or not state.get("revision") or not state.get("serverContentHash"):
        return False
    inventory = read_json(runtime.inventory_path, None)
    if not isinstance(inventory, dict):
        return False
    pending = state.get("pendingHeartbeat")
    body = pending if isinstance(pending, dict) else {
        "schemaVersion": 1,
        "source": "collector",
        "revision": state["revision"],
        "contentHash": state["serverContentHash"],
        "collectedAt": inventory.get("collectedAt"),
        "heartbeatAt": now(),
        "sources": inventory.get("sources", []),
        "machines": inventory.get("machines", {}),
    }
    state["pendingHeartbeat"] = body
    status, reply = post(str(settings["heartbeat"]), json.dumps(body, ensure_ascii=False).encode("utf-8"), str(settings["secret"]))
    sync = reply.get("sync") if isinstance(reply, dict) else None
    if status in (200, 202) and reply.get("accepted") is True and isinstance(sync, dict) and sync.get("revision") == state["revision"] and sync.get("contentHash") == state["serverContentHash"]:
        state.pop("pendingHeartbeat", None)
        state.update({"lastHeartbeatAt": body["heartbeatAt"], "syncStatus": "synced", "syncDetail": "", "retryAfter": 0, "retryCount": 0})
        return True
    if status == 409:
        state["syncStatus"] = "pending-newer"
        state.pop("pendingHeartbeat", None)
        return False
    state["syncStatus"] = "retry"
    state["retryAfter"] = time.time() + 30
    return False


def publish_stats(runtime: Runtime, force: bool = False) -> bool:
    """Refresh the usage snapshot and upload it."""
    state = runtime.state
    clock = time.monotonic()
    if not force and clock - state.get("statsProbeMonotonic", 0) < STATS_PROBE_SECONDS:
        return False
    state["statsProbeMonotonic"] = clock
    try:
        snapshot = usage.snapshot(runtime.config, runtime.usage_root, runtime.usage_state_path)
    except OSError:
        state["statsStatus"] = "failed"
        return False
    write_json_atomic(runtime.stats_path, snapshot)
    settings, error = sync_settings(runtime)
    if error:
        state["statsStatus"] = "local-only"
        return False
    body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    if len(body) > 32_768:
        state["statsStatus"] = "too-large"
        return False
    status, reply = post(str(settings["stats"]), body, str(settings["secret"]))
    accepted = status in (200, 202) and (reply.get("accepted") is True or reply.get("idempotent") is True)
    state["statsStatus"] = "synced" if accepted else ("stale" if status == 409 else "retry")
    return accepted


def cycle(runtime: Runtime, force: bool = False) -> None:
    """One full pass: refresh, publish or heartbeat, then statistics."""
    state = runtime.state
    settings, error = sync_settings(runtime)
    if not error:
        reset_remote_state(state, settings)
    changed = refresh(runtime, force or bool(state.pop("forceRefresh", False)))
    sent_heartbeat = False
    waiting = state.get("syncStatus") == "retry" and time.time() < state.get("retryAfter", 0)
    if not waiting:
        if state.get("pendingHeartbeat") and state.get("syncStatus") == "retry":
            heartbeat(runtime)
            sent_heartbeat = True
        elif changed or not state.get("inventoryDigest") or state.get("syncStatus") == "pending-newer" or state.get("syncStatus") == "retry":
            publish(runtime)
        if state.get("syncStatus") == "synced" and not sent_heartbeat:
            heartbeat(runtime)
        publish_stats(runtime, force)
    write_json_atomic(runtime.state_path, state, mode=0o600)
    runtime.log("cycle changed=" + str(changed).lower() + " sync=" + str(state.get("syncStatus", "local-only")) + " stats=" + str(state.get("statsStatus", "idle")))


def load_state(runtime: Runtime) -> dict[str, Any]:
    """Read the persisted state, dropping process-local timing values."""
    state = read_json(runtime.state_path, {})
    state = state if isinstance(state, dict) else {}
    for key in list(state):
        if key.endswith("ProbeMonotonic"):
            state.pop(key)
    return state


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Keep the guild site in step with this machine.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report whether syncing is configured")
    mode.add_argument("--once", action="store_true", help="run a single cycle")
    mode.add_argument("--run", action="store_true", help="loop until stopped")
    parser.add_argument("--config", help="path to a guild configuration file")
    parser.add_argument("--sync-config", help="path to the sync configuration file")
    parser.add_argument("--state", help="path to the sync state file")
    parser.add_argument("--usage-state", help="path to the usage scan cache")
    parser.add_argument("--usage-root", help="directory that holds the CLI log folders")
    parser.add_argument("--data-dir", help="directory for inventory.json, stats.json and parts")
    parser.add_argument("--log", help="path to the status log")
    parser.add_argument("--allow-http", action="store_true", help="permit an http endpoint for local testing")
    parser.add_argument("--interval", type=float, default=LOOP_SECONDS, help="seconds between cycles in --run mode")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    runtime = Runtime(arguments)
    if arguments.check:
        _, error = sync_settings(runtime)
        print(error or "ready")
        return 1 if error else 0

    lock_path = runtime.state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another sync process is already running")
            return 0
        runtime.state = load_state(runtime)
        if arguments.once:
            cycle(runtime, force=True)
            print("sync=" + str(runtime.state.get("syncStatus", "local-only")) + " stats=" + str(runtime.state.get("statsStatus", "idle")))
            return 0
        while True:
            cycle(runtime)
            time.sleep(max(1.0, arguments.interval))


if __name__ == "__main__":
    raise SystemExit(main())
