"""Shared helpers for the AI Guild collector scripts.

Configuration loading, home-relative display paths, timestamps, slugs, atomic
JSON writes, machine-id helpers and a safe subprocess wrapper live here so that
every script behaves identically.

``scan.py`` intentionally repeats a handful of these primitives instead of
importing them: it must run as a single standalone file that can be piped into a
remote interpreter, where this package is not installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = "guild.config.json"
EXAMPLE_CONFIG_NAME = "guild.config.example.json"

MACHINE_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9@-]{1,160}$")
SAFE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")

CATEGORY_ORDER = ("model", "cli", "connection", "agent", "harness", "skill", "automation")
STATUSES = ("observed", "configured", "attention", "unknown")
SCHEDULE_KINDS = ("calendar", "interval", "resident", "ondemand", "other")

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "UTC",
    "machines": [{"id": "main", "label": "This machine", "kind": "local"}],
    "theme": {"preset": "lavender-toybox"},
    "art": {
        "style": "soft 3D illustration on a white backdrop",
        "characterConcept": "friendly animal companions, each holding one tool that shows their job",
        "subjects": [
            "elephant archivist with a compass",
            "red panda engineer with a tiny wrench",
            "owl analyst with a telescope",
            "beaver builder with a blueprint",
        ],
        "toolStyle": "soft 3D object render on a pure white backdrop; a single practical object, no animals, no faces, no text",
        "constraints": "no text, no logos, no watermark, no interface, no real people, no private data",
        "provider": "manual",
        "dailyLimit": 10,
    },
    "roles": {},
    "collector": {
        "roots": {"claude": "~/.claude", "codex": "~/.codex", "sharedSkills": "~/.agents/skills"},
        "projectRoots": ["~/Projects"],
        "clis": ["claude", "codex", "node", "python3"],
        "jobs": {"includePatterns": ["*"], "excludePatterns": ["com.apple.*"], "privatePatterns": []},
        "usage": {"providers": ["codex", "claude"]},
    },
    "sync": {"endpoint": "", "staleAfterSeconds": 180},
}


class CommandResult(NamedTuple):
    """Outcome of a bounded, shell-free subprocess call."""

    ok: bool
    code: int
    stdout: str


def home() -> Path:
    """Current home directory (honours ``HOME``, which keeps tests hermetic)."""
    return Path(os.path.expanduser("~"))


def now() -> str:
    """Current time as an ISO-8601 UTC string with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_iso(value: datetime) -> str:
    """Render a datetime as the ISO-8601 UTC string the schema expects."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    """Lowercase identifier fragment made of ``a-z``, ``0-9`` and hyphens."""
    cleaned = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(value).lower())).strip("-")
    return cleaned or "item"


def display_path(path: Path | str, base: Path | None = None) -> str:
    """Home-relative display path; never returns an absolute home path."""
    root = base or home()
    candidate = Path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        text = candidate.as_posix()
        if "/Users/" in text or "/home/" in text:
            return candidate.name
        return text
    return "~" if str(relative) == "." else "~/" + relative.as_posix()


def truncate(value: str, limit: int) -> str:
    """Collapse whitespace and cut a string down to ``limit`` characters."""
    collapsed = re.sub(r"\s+", " ", str(value)).strip()
    return collapsed[:limit]


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file is missing or invalid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, data: Any, mode: int = 0o644) -> Path:
    """Write pretty JSON to ``path`` atomically with explicit permissions."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge ``override`` onto ``base`` without mutating either."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return override


def config_path(explicit: str | Path | None = None, root: Path | None = None) -> Path | None:
    """Locate the configuration file: explicit, then real, then example."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    base = root or REPO_ROOT
    for name in (CONFIG_NAME, EXAMPLE_CONFIG_NAME):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: str | Path | None = None, root: Path | None = None) -> dict[str, Any]:
    """Load the guild configuration merged onto built-in defaults."""
    found = config_path(explicit, root)
    raw = read_json(found, {}) if found else {}
    if not isinstance(raw, dict):
        raw = {}
    merged = deep_merge(DEFAULT_CONFIG, raw)
    merged["_configPath"] = str(found) if found else ""
    return merged


def machines(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Configured machines with valid ids, in configuration order."""
    rows = config.get("machines")
    result: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and is_machine_id(row.get("id")):
                result.append(row)
    return result or [dict(DEFAULT_CONFIG["machines"][0])]


def is_machine_id(value: Any) -> bool:
    """True when ``value`` is a valid machine id."""
    return isinstance(value, str) and bool(MACHINE_ID_PATTERN.match(value))


def local_machine(config: dict[str, Any]) -> dict[str, Any]:
    """First machine declared with ``kind: local``, else the first machine."""
    rows = machines(config)
    for row in rows:
        if row.get("kind") == "local":
            return row
    return rows[0]


def remote_machines(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Machines declared with ``kind: ssh`` and a host to reach them at."""
    return [row for row in machines(config) if row.get("kind") == "ssh" and isinstance(row.get("host"), str) and row["host"]]


def run_command(args: Sequence[str], timeout: float = 10.0, cwd: Path | None = None, env: dict[str, str] | None = None) -> CommandResult:
    """Run a command without a shell, capturing bounded text output."""
    try:
        completed = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return CommandResult(False, -1, "")
    return CommandResult(completed.returncode == 0, completed.returncode, completed.stdout or "")
