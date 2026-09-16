#!/usr/bin/env python3
"""Read-only inventory scan of a local AI environment.

The scan walks five modules -- runtime, skills, agents, harness and automation --
and writes an inventory document that satisfies ``docs/INVENTORY-SCHEMA.md``.
It reads names, versions, trigger metadata and public frontmatter only: never
environment values, command arguments, tokens, prompts or file bodies.

This file is deliberately standalone.  A remote machine is scanned by piping
this exact source into its interpreter (``ssh host python3 - --stdout``), where
the rest of the package is not available, so the few helpers it shares with
``common.py`` are repeated here rather than imported.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # Python 3.11+ parses TOML natively; older remotes fall back to a regex reader.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on old interpreters
    tomllib = None

try:
    import plistlib
except ModuleNotFoundError:  # pragma: no cover - non-macOS interpreters without plistlib
    plistlib = None

MODULES = ("runtime", "skills", "agents", "harness", "automation")
CATEGORY_ORDER = ("model", "cli", "connection", "agent", "harness", "skill", "automation")
MAX_ITEMS = 2_500
MAX_EVIDENCE = 50
VERSION_LINE_LIMIT = 160
SUMMARY_LIMIT = 300
CLI_TIMEOUT_SECONDS = 10
REMOTE_TIMEOUT_SECONDS = 60
PROJECT_SCAN_DEPTH = 3
SKIP_DIRECTORIES = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"}
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
MODEL_KEYS = ("model", "default_model", "model_name")

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "UTC",
    "machines": [{"id": "main", "label": "This machine", "kind": "local"}],
    "roles": {},
    "collector": {
        "roots": {"claude": "~/.claude", "codex": "~/.codex", "sharedSkills": "~/.agents/skills"},
        "projectRoots": ["~/Projects"],
        "clis": ["claude", "codex", "gemini", "grok", "goose", "gh", "node", "python3"],
        "jobs": {"includePatterns": ["*"], "excludePatterns": ["com.apple.*"], "privatePatterns": []},
    },
}

REDACTIONS = (
    (re.compile(r"/(?:Users|home)/[^\s\"'`)\]]*"), "[path]"),
    (re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"), "[redacted]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{15,}"), "[redacted]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[redacted]"),
    (re.compile(r"AIza[A-Za-z0-9_-]{25,}"), "[redacted]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"), "[redacted]"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"), "[redacted]"),
)

NOTES = {
    "runtime": "Versions are the local `--version` output at scan time. No model was run and no network request was made.",
    "connection": "Connection entries record only that a name is configured. Authentication and reachability were not tested.",
    "skills": "Skill and agent summaries are copied from local frontmatter as written, without translation or rewriting.",
    "automation": "Scheduled jobs expose only their trigger metadata. Program arguments, environment values and logs are never collected.",
    "privacy": "Configuration values, tokens, environment variables, prompts and file contents are out of scope for this scan.",
}


# --------------------------------------------------------------------------- #
# Standalone primitives (mirrored in common.py; see the module docstring).
# --------------------------------------------------------------------------- #

def home() -> Path:
    """Current home directory, honouring ``HOME`` so tests stay hermetic."""
    return Path(os.path.expanduser("~"))


def now() -> str:
    """Current time as an ISO-8601 UTC string with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    """Lowercase identifier fragment made of ``a-z``, ``0-9`` and hyphens."""
    cleaned = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(value).lower())).strip("-")
    return cleaned or "item"


def expand(value: str, base: Path | None = None) -> Path:
    """Expand ``~`` in a configured path against the current home directory."""
    text = str(value)
    root = base or home()
    if text.startswith("~"):
        return Path(str(root) + text[1:])
    return Path(text)


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
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file is missing or invalid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_text(path: Path, limit: int = 262_144) -> str:
    """Read a bounded amount of text, returning an empty string on failure."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def run_command(args: Sequence[str], timeout: float = CLI_TIMEOUT_SECONDS, stdin_text: str | None = None) -> tuple[bool, str]:
    """Run a command without a shell and capture bounded text output."""
    try:
        completed = subprocess.run(
            list(args),
            input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if stdin_text is not None else subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return completed.returncode == 0, completed.stdout or ""


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge ``override`` onto ``base`` without mutating either."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return override


def repo_root() -> Path | None:
    """Repository root when this file is on disk; ``None`` when piped in."""
    try:
        return Path(__file__).resolve().parents[1]
    except NameError:  # executed from stdin on a remote machine
        return None


def load_config(explicit: str | None = None) -> dict[str, Any]:
    """Load the guild configuration merged onto the built-in defaults."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        root = repo_root()
        if root:
            candidates.extend([root / "guild.config.json", root / "guild.config.example.json"])
    for candidate in candidates:
        if candidate.is_file():
            raw = read_json(candidate, {})
            if isinstance(raw, dict):
                return deep_merge(DEFAULT_CONFIG, raw)
    return dict(DEFAULT_CONFIG)


def clean(value: str, base: Path | None = None) -> str:
    """Strip home paths and secret-shaped fragments out of emitted text."""
    text = str(value)
    root = str(base or home())
    if root and root != "/":
        text = text.replace(root, "~")
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def sanitize(value: Any, base: Path | None = None) -> Any:
    """Recursively apply :func:`clean` to every string in a document."""
    if isinstance(value, str):
        return clean(value, base)
    if isinstance(value, list):
        return [sanitize(entry, base) for entry in value]
    if isinstance(value, dict):
        return {key: sanitize(entry, base) for key, entry in value.items()}
    return value


def frontmatter(path: Path) -> dict[str, str]:
    """Parse the YAML-ish frontmatter block of a Markdown file.

    Handles quoted scalars, folded/literal blocks (``>`` and ``|``) and plain
    scalars continued on following indented lines.
    """
    text = read_text(path, 32_768)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    lines = text[3:end].splitlines()
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = re.match(r"^([\w.-]+):[ \t]*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        key, inline = match.group(1), match.group(2).strip()
        folded = inline[:1] in (">", "|")
        continuation: list[str] = []
        if folded or not inline:
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip() and not line[:1].isspace():
                    break
                if line.strip():
                    continuation.append(line.strip())
                index += 1
            index -= 1
        else:
            look = index + 1
            while look < len(lines):
                line = lines[look]
                if not line.strip() or not line[:1].isspace() or re.match(r"^\s+[\w.-]+:\s", line) or line.strip().startswith("-"):
                    break
                continuation.append(line.strip())
                look += 1
            index = look - 1
        value = " ".join([inline] if not folded and inline else []) + (" " if (inline and not folded and continuation) else "")
        value = (value + " ".join(continuation)).strip()
        values[key] = value.strip().strip('"').strip("'")
        index += 1
    return values


def toml_table_names(path: Path, table: str) -> list[str]:
    """Names of the sub-tables of ``table``; values are never read."""
    text = read_text(path)
    if not text:
        return []
    if tomllib is not None:
        try:
            parsed = tomllib.loads(text)
        except Exception:
            parsed = {}
        section = parsed.get(table)
        if isinstance(section, dict):
            return [str(name) for name in section]
        return []
    names: list[str] = []
    for match in re.finditer(r"^\s*\[" + re.escape(table) + r"\.([^\]]+)\]", text, re.MULTILINE):
        names.append(match.group(1).strip().strip('"'))
    return names


def toml_scalars(path: Path, keys: Iterable[str]) -> dict[str, str]:
    """Read top-level quoted scalar keys from a TOML file."""
    text = read_text(path)
    values: dict[str, str] = {}
    for key in keys:
        match = re.search(r'^\s*' + re.escape(key) + r'\s*=\s*"([^"]*)"\s*$', text, re.MULTILINE)
        if match:
            values[key] = match.group(1)
    return values


def safe_model_values(values: Iterable[Any]) -> list[str]:
    """Keep only model-shaped scalars, so no configuration body can leak."""
    result: list[str] = []
    for value in values:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and SAFE_VALUE.match(str(value)):
            result.append(str(value))
    return list(dict.fromkeys(result))


def nested_model_values(data: Any) -> list[str]:
    """Collect values of keys literally named after a model, at any depth."""
    found: list[Any] = []
    stack = [data]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key in MODEL_KEYS:
                if key in current:
                    found.append(current[key])
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return safe_model_values(found)


# --------------------------------------------------------------------------- #
# Scan context and item helpers
# --------------------------------------------------------------------------- #

class Context:
    """Everything a module needs: configuration, machine id and timestamp."""

    def __init__(self, config: dict[str, Any], machine: str, checked_at: str | None = None) -> None:
        self.config = config
        self.machine = machine
        self.checked_at = checked_at or now()
        self.home = home()
        collector = config.get("collector", {})
        self.collector = collector if isinstance(collector, dict) else {}
        roles = config.get("roles", {})
        self.roles = roles if isinstance(roles, dict) else {}
        self.private_labels: list[str] = []

    def path(self, value: str) -> Path:
        """Expand a configured path against this context's home directory."""
        return expand(value, self.home)

    def show(self, value: Path | str) -> str:
        """Display path relative to this context's home directory."""
        return display_path(value, self.home)

    def evidence(self, source: str, detail: str) -> dict[str, str]:
        """One evidence record: what was checked, where, and when."""
        return {"source": truncate(source, 500), "detail": truncate(detail, 1_000), "checkedAt": self.checked_at}


def make_item(
    context: Context,
    identifier: str,
    name: str,
    category: str,
    summary: str,
    *,
    status: str,
    status_label: str,
    tags: Sequence[str],
    runtimes: Sequence[str],
    evidence: Sequence[dict[str, str]],
    command: str | None = None,
    schedule: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    related: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build one schema-shaped inventory item."""
    item: dict[str, Any] = {
        "id": identifier[:160],
        "name": truncate(name, 400),
        "category": category,
        "summary": truncate(summary, 4_000),
        "tags": [truncate(tag, 120) for tag in dict.fromkeys(tags) if tag][:50],
        "status": status,
        "statusLabel": truncate(status_label, 500),
        "machines": [context.machine],
        "runtimes": [truncate(runtime, 120) for runtime in dict.fromkeys(runtimes) if runtime],
        "evidence": list(evidence)[:MAX_EVIDENCE],
    }
    if related:
        item["relatedIds"] = list(dict.fromkeys(related))
    if command:
        item["command"] = truncate(command, 1_000)
    if schedule:
        item["schedule"] = schedule
    if meta:
        item["meta"] = meta
    return item


# --------------------------------------------------------------------------- #
# Module: runtime (models, CLIs, connections)
# --------------------------------------------------------------------------- #

def collect_models(context: Context) -> list[dict[str, Any]]:
    """Model names recorded in local CLI configuration files."""
    items: list[dict[str, Any]] = []
    codex_root = context.path(str(context.collector.get("roots", {}).get("codex", "~/.codex")))
    claude_root = context.path(str(context.collector.get("roots", {}).get("claude", "~/.claude")))

    cache = codex_root / "models_cache.json"
    payload = read_json(cache, {})
    models = payload.get("models") if isinstance(payload, dict) else None
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        name = safe_model_values([model.get("slug")])
        if not name:
            continue
        identifier = name[0]
        meta: dict[str, Any] = {"model": identifier, "provider": "codex"}
        reasoning = safe_model_values([model.get("default_reasoning_level")])
        if reasoning:
            meta["defaultReasoning"] = reasoning[0]
        if isinstance(model.get("context_window"), int) and not isinstance(model.get("context_window"), bool):
            meta["contextWindow"] = model["context_window"]
        display = model.get("display_name")
        items.append(make_item(
            context, "model-" + slug(identifier), display if isinstance(display, str) and display else identifier, "model",
            "Model offered by the local Codex model list.",
            status="configured", status_label="Present in the local model cache.",
            tags=["codex", "model"], runtimes=["codex"],
            evidence=[context.evidence(context.show(cache), "Model listed as selectable: " + identifier)],
            meta=meta,
        ))

    settings = claude_root / "settings.json"
    config = read_json(settings, {})
    config = config if isinstance(config, dict) else {}
    fallback = config.get("fallbackModel")
    candidates = [config.get("model")]
    candidates.extend(fallback if isinstance(fallback, list) else [fallback])
    model_settings = config.get("modelSettings")
    if isinstance(model_settings, dict):
        candidates.extend(model_settings.keys())
    for identifier in safe_model_values(candidates):
        items.append(make_item(
            context, "model-claude-" + slug(identifier), identifier, "model",
            "Model selected in the local Claude Code settings.",
            status="configured", status_label="Named in the local settings file.",
            tags=["claude", "model"], runtimes=["claude"],
            evidence=[context.evidence(context.show(settings), "Model key found in settings")],
            meta={"model": identifier, "provider": "claude"},
        ))

    sources = (
        (context.path("~/.gemini/settings.json"), "json", "gemini"),
        (context.path("~/.config/goose/config.yaml"), "yaml", "goose"),
        (context.path("~/.grok/config.toml"), "toml", "grok"),
    )
    for path, kind, provider in sources:
        if not path.is_file():
            continue
        if kind == "json":
            values = nested_model_values(read_json(path, {}))
        elif kind == "toml" and tomllib is not None:
            try:
                values = nested_model_values(tomllib.loads(read_text(path)))
            except Exception:
                values = []
        else:
            pattern = r"^\s*(?:" + "|".join(MODEL_KEYS) + r")\s*[:=]\s*[\"']?([^\s\"'#]+)"
            values = safe_model_values(re.findall(pattern, read_text(path), re.MULTILINE))
        for identifier in values:
            items.append(make_item(
                context, "model-" + provider + "-" + slug(identifier), identifier, "model",
                "Model named in the local " + provider + " configuration.",
                status="configured", status_label="Named in a local configuration file.",
                tags=[provider, "model"], runtimes=[provider],
                evidence=[context.evidence(context.show(path), "Model key found in configuration")],
                meta={"model": identifier, "provider": provider},
            ))
    return items


def collect_clis(context: Context) -> list[dict[str, Any]]:
    """Command line tools from the configured list, with their version line."""
    names = context.collector.get("clis")
    items: list[dict[str, Any]] = []
    for name in names if isinstance(names, list) else []:
        if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9._-]{1,64}$", name):
            continue
        executable = shutil.which(name)
        if not executable:
            continue
        ok, output = run_command([name, "--version"], timeout=CLI_TIMEOUT_SECONDS)
        first = next((line.strip() for line in output.splitlines() if line.strip()), "")
        detail = first[:VERSION_LINE_LIMIT] if ok and first else "Executable found; version output unavailable."
        items.append(make_item(
            context, "cli-" + slug(name), name, "cli",
            "Command line tool available on this machine.",
            status="observed", status_label="Executable found on PATH at scan time.",
            tags=["cli"], runtimes=[name],
            evidence=[context.evidence(name + " --version", detail)],
            command=name + " --version",
            meta={"executable": context.show(executable)},
        ))
    return items


def mcp_names(payload: Any, key: str = "mcpServers") -> list[str]:
    """Server names from an MCP registry object; values are never read."""
    if not isinstance(payload, dict):
        return []
    servers = payload.get(key)
    return [str(name) for name in servers] if isinstance(servers, dict) else []


def find_project_mcp_files(roots: Iterable[Path], depth: int = PROJECT_SCAN_DEPTH) -> list[Path]:
    """``.mcp.json`` files below the configured project roots, depth limited."""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for current, directories, files in os.walk(root):
            current_path = Path(current)
            level = len(current_path.parts) - base_depth
            if level >= depth:
                directories[:] = []
            else:
                directories[:] = [name for name in directories if name not in SKIP_DIRECTORIES and not name.startswith(".")]
            if ".mcp.json" in files:
                found.append(current_path / ".mcp.json")
    return sorted(found)


def collect_connections(context: Context) -> list[dict[str, Any]]:
    """MCP server names, enabled plugins and pending authentications."""
    items: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}

    def add(identifier: str, name: str, summary: str, status: str, status_label: str, tags: Sequence[str], runtimes: Sequence[str], source: str, detail: str) -> None:
        existing = index.get(identifier)
        if existing:
            if len(existing["evidence"]) < MAX_EVIDENCE:
                existing["evidence"].append(context.evidence(source, detail))
            return
        item = make_item(
            context, identifier, name, "connection", summary,
            status=status, status_label=status_label, tags=tags, runtimes=runtimes,
            evidence=[context.evidence(source, detail)],
        )
        index[identifier] = item
        items.append(item)

    codex_root = context.path(str(context.collector.get("roots", {}).get("codex", "~/.codex")))
    claude_root = context.path(str(context.collector.get("roots", {}).get("claude", "~/.claude")))

    codex_config = codex_root / "config.toml"
    for name in toml_table_names(codex_config, "mcp_servers"):
        add("connection-codex-mcp-" + slug(name), name, "Tool connection registered with the Codex CLI.",
            "configured", "Registered in local configuration. Connection and authentication were not tested.",
            ["mcp", "codex"], ["codex", "mcp"], context.show(codex_config), "Server name found in the MCP table")

    registry_path = context.home / ".claude.json"
    registry = read_json(registry_path, {})
    for name in mcp_names(registry):
        add("connection-claude-mcp-" + slug(name), name, "Tool connection registered with Claude Code.",
            "configured", "Registered in local configuration. Connection and authentication were not tested.",
            ["mcp", "claude"], ["claude", "mcp"], context.show(registry_path), "Server name found in the global registry")
    projects = registry.get("projects") if isinstance(registry, dict) else None
    for project in (projects or {}).values() if isinstance(projects, dict) else []:
        for name in mcp_names(project):
            add("connection-claude-project-mcp-" + slug(name), name, "Tool connection registered for one project.",
                "configured", "Registered for a project scope. Connection and authentication were not tested.",
                ["mcp", "claude", "project"], ["claude", "mcp"], context.show(registry_path), "Server name found in a project scope")

    project_roots = context.collector.get("projectRoots")
    roots = [context.path(str(entry)) for entry in project_roots if isinstance(entry, str)] if isinstance(project_roots, list) else []
    for path in find_project_mcp_files(roots):
        for name in mcp_names(read_json(path, {})):
            add("connection-project-mcp-" + slug(name), name, "Tool connection shipped with a local project.",
                "configured", "Declared in a project file. Connection and authentication were not tested.",
                ["mcp", "project"], ["mcp"], context.show(path), "Server name found in a project file")

    settings = claude_root / "settings.json"
    config = read_json(settings, {})
    plugins = config.get("enabledPlugins") if isinstance(config, dict) else None
    for name in plugins if isinstance(plugins, dict) else []:
        add("connection-claude-plugin-" + slug(str(name)), str(name), "Plugin enabled in Claude Code.",
            "configured", "Enabled in local settings. The tools it provides were not contacted.",
            ["plugin", "claude"], ["claude", "plugin"], context.show(settings), "Plugin name found in settings")

    auth_cache = claude_root / "mcp-needs-auth-cache.json"
    cached = read_json(auth_cache, {})
    for name in cached if isinstance(cached, dict) else []:
        add("connection-claude-auth-" + slug(str(name)), str(name), "Tool connection waiting for sign-in.",
            "attention", "Listed in the authentication cache. Sign-in may be required before use.",
            ["mcp", "claude", "authentication"], ["claude", "mcp"], context.show(auth_cache), "Server name found in the authentication cache")
    return items


def collect_runtime(context: Context) -> list[dict[str, Any]]:
    """Models, command line tools and tool connections."""
    return collect_models(context) + collect_clis(context) + collect_connections(context)


# --------------------------------------------------------------------------- #
# Module: skills
# --------------------------------------------------------------------------- #

def skill_roots(context: Context) -> list[tuple[Path, str, str, str]]:
    """(path, tag, label, runtime) for every configured skill location."""
    roots = context.collector.get("roots", {})
    roots = roots if isinstance(roots, dict) else {}
    claude = context.path(str(roots.get("claude", "~/.claude")))
    codex = context.path(str(roots.get("codex", "~/.codex")))
    shared = context.path(str(roots.get("sharedSkills", "~/.agents/skills")))
    return [
        (shared, "shared", "Shared skills", "shared"),
        (codex / "skills", "codex", "Codex skills", "codex"),
        (codex / "plugins" / "cache", "plugin", "Codex plugin cache", "codex"),
        (claude / "skills", "claude", "Claude skills", "claude"),
        (claude / "plugins" / "cache", "plugin", "Claude plugin cache", "claude"),
    ]


def collect_skills(context: Context) -> list[dict[str, Any]]:
    """Every ``SKILL.md`` found under the configured skill roots."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    seen_paths: set[str] = set()
    for root, tag, label, runtime in skill_roots(context):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            try:
                resolved = str(path.resolve())
            except OSError:
                continue
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            meta = frontmatter(path)
            name = meta.get("name") or path.parent.name
            description = truncate(meta.get("description", ""), 800)
            identifier = "skill-" + slug(name)
            record = grouped.get(identifier)
            if record is None:
                record = make_item(
                    context, identifier, name, "skill",
                    description[:SUMMARY_LIMIT] or "Local skill definition without a description.",
                    status="configured", status_label="SKILL.md found locally. Execution was not attempted.",
                    tags=[tag], runtimes=[runtime], evidence=[],
                    meta={"sources": [], "sourceCount": 0, "summarySource": "frontmatter" if description else "folder-name"},
                )
                if description:
                    record["meta"]["descriptionOriginal"] = description
                grouped[identifier] = record
                order.append(identifier)
            if tag not in record["tags"]:
                record["tags"].append(tag)
            if runtime not in record["runtimes"]:
                record["runtimes"].append(runtime)
            record["meta"]["sources"].append({"kind": tag, "label": label})
            record["meta"]["sourceCount"] = len(record["meta"]["sources"])
            if len(record["evidence"]) < MAX_EVIDENCE:
                record["evidence"].append(context.evidence(context.show(path), "SKILL.md found on disk"))
    return [grouped[identifier] for identifier in order]


# --------------------------------------------------------------------------- #
# Module: agents
# --------------------------------------------------------------------------- #

def derive_strengths(description: str) -> list[str]:
    """Two to four short phrases taken from an agent's own description."""
    parts = [truncate(part, 60) for part in re.split(r"[/,、・]", description) if truncate(part, 60)]
    if len(parts) < 2:
        parts = [truncate(part, 60) for part in re.split(r"(?<=[.!?。])\s+", description) if truncate(part, 60)]
    return parts[:4]


def first_sentence(description: str) -> str:
    """The first sentence of a description, used as an example request."""
    text = truncate(description, 400)
    if not text:
        return ""
    return truncate(re.split(r"(?<=[.!?。])\s+", text)[0], 200)


def agent_profile(context: Context, name: str, description: str) -> dict[str, Any]:
    """Role metadata from configuration, falling back to the description."""
    configured = context.roles.get(name)
    configured = configured if isinstance(configured, dict) else {}
    profile: dict[str, Any] = {}
    title = configured.get("title")
    if isinstance(title, str) and title.strip():
        profile["title"] = truncate(title, 120)
    strengths = configured.get("strengths")
    if isinstance(strengths, list) and strengths:
        profile["strengths"] = [truncate(str(entry), 120) for entry in strengths][:4]
    else:
        profile["strengths"] = derive_strengths(description)
    example = configured.get("exampleTask")
    if isinstance(example, str) and example.strip():
        profile["exampleTask"] = truncate(example, 300)
    else:
        profile["exampleTask"] = first_sentence(description)
    if not profile["strengths"]:
        profile["strengths"] = [truncate(name, 60) + " tasks"]
    if not profile["exampleTask"]:
        profile["exampleTask"] = "Delegate a " + truncate(name, 60) + " task."
    return profile


def add_agent(
    context: Context,
    registry: dict[str, dict[str, Any]],
    order: list[str],
    name: str,
    description: str,
    runtime: str,
    origin: str,
    model_policy: str,
    source: Path,
    detail: str,
) -> None:
    """Insert or merge one agent definition into the roster."""
    identifier = "agent-" + slug(name)
    profile = agent_profile(context, name, description)
    variant = {"origin": origin, "modelPolicy": model_policy, "sourceFile": context.show(source)}
    record = registry.get(identifier)
    if record is None:
        meta: dict[str, Any] = {
            "role": truncate(description, 400) or "Reusable agent definition.",
            "strengths": profile["strengths"],
            "exampleTask": profile["exampleTask"],
            "origin": origin,
            "modelPolicy": model_policy,
            "variants": [variant],
            "sourceFile": context.show(source),
        }
        if "title" in profile:
            meta["title"] = profile["title"]
        if description:
            meta["descriptionOriginal"] = truncate(description, 800)
        record = make_item(
            context, identifier, name, "agent",
            truncate(description, SUMMARY_LIMIT) or "Reusable agent definition found locally.",
            status="configured", status_label="Agent definition found locally. It was not started.",
            tags=["agent", runtime], runtimes=[runtime],
            evidence=[context.evidence(context.show(source), detail)],
            meta=meta,
        )
        registry[identifier] = record
        order.append(identifier)
        return
    record["meta"]["variants"].append(variant)
    if runtime not in record["runtimes"]:
        record["runtimes"].append(runtime)
    if runtime not in record["tags"]:
        record["tags"].append(runtime)
    if len(record["evidence"]) < MAX_EVIDENCE:
        record["evidence"].append(context.evidence(context.show(source), detail))


def collect_agents(context: Context) -> list[dict[str, Any]]:
    """Claude agent definitions, plugin agents and Codex agent profiles."""
    registry: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    seen: set[str] = set()
    roots = context.collector.get("roots", {})
    roots = roots if isinstance(roots, dict) else {}
    claude = context.path(str(roots.get("claude", "~/.claude")))
    codex = context.path(str(roots.get("codex", "~/.codex")))

    personal = claude / "agents"
    plugin_cache = claude / "plugins" / "cache"
    definitions: list[tuple[Path, str]] = []
    if personal.is_dir():
        definitions.extend((path, "personal") for path in sorted(personal.glob("*.md")))
    if plugin_cache.is_dir():
        definitions.extend((path, "plugin") for path in sorted(plugin_cache.rglob("*.md")) if "agents" in path.parts)
    for path, origin_kind in definitions:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        meta = frontmatter(path)
        name = meta.get("name") or path.stem
        model = meta.get("model", "")
        policy = "Definition names model " + model if SAFE_VALUE.match(model or "") else "No model named in the definition"
        origin = "plugin" if origin_kind == "plugin" else "personal"
        add_agent(context, registry, order, name, meta.get("description", ""), "claude", origin, policy, path, "Agent frontmatter read")

    codex_agents = codex / "agents"
    if codex_agents.is_dir():
        for path in sorted(codex_agents.glob("*.toml")):
            values = toml_scalars(path, ("model", "model_reasoning_effort"))
            configured = " / ".join(value for value in (values.get("model"), values.get("model_reasoning_effort")) if value and SAFE_VALUE.match(value))
            policy = "Definition names " + configured if configured else "No model named in the definition"
            add_agent(context, registry, order, path.stem, "", "codex", "native", policy, path, "Agent profile keys read")
            record = registry["agent-" + slug(path.stem)]
            if values.get("model") and SAFE_VALUE.match(values["model"]):
                record["meta"]["model"] = values["model"]
            if values.get("model_reasoning_effort") and SAFE_VALUE.match(values["model_reasoning_effort"]):
                record["meta"]["effort"] = values["model_reasoning_effort"]
    return [registry[identifier] for identifier in order]


# --------------------------------------------------------------------------- #
# Module: harness
# --------------------------------------------------------------------------- #

def collect_harness(context: Context) -> list[dict[str, Any]]:
    """Hook events, rule documents and the presence of instruction files."""
    items: list[dict[str, Any]] = []
    roots = context.collector.get("roots", {})
    roots = roots if isinstance(roots, dict) else {}
    claude = context.path(str(roots.get("claude", "~/.claude")))
    codex = context.path(str(roots.get("codex", "~/.codex")))

    settings = claude / "settings.json"
    config = read_json(settings, {})
    hooks = config.get("hooks") if isinstance(config, dict) else None
    for event, groups in (hooks or {}).items() if isinstance(hooks, dict) else []:
        if not isinstance(groups, list):
            continue
        items.append(make_item(
            context, "harness-hook-" + slug(str(event)), str(event), "harness",
            "Local automation that runs on the " + str(event) + " event.",
            status="configured", status_label="Hook configured locally. Command text is not collected.",
            tags=["harness", "hook", "claude"], runtimes=["claude"],
            evidence=[context.evidence(context.show(settings), "Hook event found with " + str(len(groups)) + " group(s)")],
            meta={"event": truncate(str(event), 120), "hookGroups": len(groups)},
        ))

    rules = claude / "rules"
    if rules.is_dir():
        for path in sorted(rules.glob("*.md")):
            summary = ""
            for line in read_text(path, 16_384).splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                    summary = truncate(stripped, 200)
                    break
            items.append(make_item(
                context, "harness-rule-" + slug(path.stem), path.stem, "harness",
                summary or "Local rule document loaded into agent sessions.",
                status="configured", status_label="Rule document found locally.",
                tags=["harness", "rules"], runtimes=["claude"],
                evidence=[context.evidence(context.show(path), "Rule document found on disk")],
                meta={"sourceFile": context.show(path)},
            ))

    for path, identifier, name, runtime, summary in (
        (claude / "CLAUDE.md", "harness-claude-instructions", "Claude instructions", "claude", "Global instruction file applied to Claude Code sessions."),
        (codex / "AGENTS.md", "harness-codex-instructions", "Codex instructions", "codex", "Global instruction file applied to Codex sessions."),
    ):
        if path.is_file():
            items.append(make_item(
                context, identifier, name, "harness", summary,
                status="configured", status_label="Instruction file present. Its contents are not collected.",
                tags=["harness", "instructions", runtime], runtimes=[runtime],
                evidence=[context.evidence(context.show(path), "Instruction file found on disk")],
                meta={"sourceFile": context.show(path)},
            ))
    return items


# --------------------------------------------------------------------------- #
# Module: automation
# --------------------------------------------------------------------------- #

def job_patterns(context: Context) -> tuple[list[str], list[str], list[str]]:
    """Include, exclude and private label patterns from configuration."""
    jobs = context.collector.get("jobs", {})
    jobs = jobs if isinstance(jobs, dict) else {}

    def patterns(key: str, fallback: list[str]) -> list[str]:
        value = jobs.get(key)
        return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else fallback

    return patterns("includePatterns", ["*"]), patterns("excludePatterns", []), patterns("privatePatterns", [])


def label_selected(label: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    """True when a scheduler label passes the include/exclude filters."""
    if include and not any(fnmatch.fnmatch(label, pattern) for pattern in include):
        return False
    return not any(fnmatch.fnmatch(label, pattern) for pattern in exclude)


def launchctl_rows() -> dict[str, tuple[int | None, int | None]]:
    """Label to (pid, last exit code); job arguments are never read."""
    ok, output = run_command(["launchctl", "list"], timeout=20)
    rows: dict[str, tuple[int | None, int | None]] = {}
    if not ok:
        return rows
    for line in output.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) != 3:
            continue
        pid_text, exit_text, label = columns
        try:
            pid = None if pid_text.strip() == "-" else int(pid_text)
            code = None if exit_text.strip() == "-" else int(exit_text)
        except ValueError:
            continue
        rows[label.strip()] = (pid, code)
    return rows


def plist_schedule(plist: dict[str, Any]) -> dict[str, Any]:
    """Trigger metadata for one launchd job, with nothing else copied."""
    calendar = plist.get("StartCalendarInterval")
    interval = plist.get("StartInterval")
    if calendar is not None:
        return {"kind": "calendar", "label": "Runs on a calendar schedule", "calendar": calendar}
    if isinstance(interval, int) and not isinstance(interval, bool):
        return {"kind": "interval", "label": "Runs every " + str(interval) + " seconds", "intervalSeconds": interval}
    if plist.get("KeepAlive"):
        return {"kind": "resident", "label": "Kept running as a resident process"}
    if plist.get("RunAtLoad"):
        return {"kind": "ondemand", "label": "Runs when loaded at login"}
    return {"kind": "other", "label": "Trigger not captured by this scan"}


def opaque_job_id(label: str) -> str:
    """Stable, one-way id for a job whose label must stay private."""
    return "automation-private-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]


def collect_launchd(context: Context) -> list[dict[str, Any]]:
    """User launchd agents plus their loaded and running state."""
    if plistlib is None:
        return []
    directory = context.home / "Library" / "LaunchAgents"
    if not directory.is_dir():
        return []
    include, exclude, private = job_patterns(context)
    rows = launchctl_rows()
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.plist")):
        try:
            raw = plistlib.loads(path.read_bytes())
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("Label") or path.stem)
        if not label_selected(label, include, exclude):
            continue
        is_private = any(fnmatch.fnmatch(label, pattern) for pattern in private)
        if is_private:
            context.private_labels.append(label)
        schedule = plist_schedule({key: raw.get(key) for key in ("StartCalendarInterval", "StartInterval", "KeepAlive", "RunAtLoad")})
        pid, code = rows.get(label, (None, None))
        schedule["loaded"] = label in rows
        schedule["running"] = pid is not None
        if code is not None:
            schedule["lastExitCode"] = code
        if code not in (None, 0):
            status, status_label = "attention", "The scheduler reported exit code " + str(code) + " for the last run."
        elif label not in rows:
            status, status_label = "configured", "Job file found. It is not currently loaded in the scheduler."
        elif pid is not None:
            status, status_label = "observed", "Loaded in the scheduler and running right now."
        else:
            status, status_label = "observed", "Loaded in the scheduler and waiting for its next trigger."
        meta: dict[str, Any] = {"runAtLoad": bool(raw.get("RunAtLoad", False))}
        if is_private:
            identifier, name = opaque_job_id(label), "Private job"
            source, detail = "~/Library/LaunchAgents (label withheld)", "Trigger metadata read; the label is not published"
            meta["schedulerId"] = identifier
        else:
            identifier, name = "automation-" + slug(label), label
            source, detail = context.show(path), "Trigger metadata and scheduler state read"
            meta["label"] = truncate(label, 200)
            meta["sourceFile"] = context.show(path)
        items.append(make_item(
            context, identifier, name, "automation",
            "Scheduled job registered with the user launchd scheduler.",
            status=status, status_label=status_label,
            tags=["launchd", schedule["kind"]], runtimes=["launchd"],
            evidence=[context.evidence(source, detail)],
            schedule=schedule, meta=meta,
        ))
    return items


def parse_crontab(text: str) -> list[tuple[str, str]]:
    """(expression, name) for each schedule line; commands are discarded."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", stripped):
            continue
        comment = ""
        if "#" in stripped:
            stripped, _, comment = stripped.partition("#")
            comment = truncate(comment, 120)
            stripped = stripped.strip()
        if stripped.startswith("@"):
            expression = stripped.split()[0]
        else:
            fields = stripped.split()
            if len(fields) < 6:
                continue
            expression = " ".join(fields[:5])
        entries.append((expression, comment))
    return entries


def collect_cron(context: Context) -> list[dict[str, Any]]:
    """User crontab entries, reduced to their schedule expressions."""
    ok, output = run_command(["crontab", "-l"], timeout=10)
    if not ok or not output.strip():
        return []
    include, exclude, private = job_patterns(context)
    items: list[dict[str, Any]] = []
    for index, (expression, comment) in enumerate(parse_crontab(output)):
        label = comment or ("cron entry " + str(index + 1))
        if not label_selected(label, include, exclude):
            continue
        is_private = any(fnmatch.fnmatch(label, pattern) for pattern in private)
        if is_private:
            context.private_labels.append(label)
        schedule = {"kind": "calendar", "label": "Runs on a cron schedule", "calendar": {"expression": truncate(expression, 120)}, "loaded": True}
        meta: dict[str, Any] = {}
        if is_private or not comment:
            identifier = opaque_job_id(label + "|" + expression)
            name = "Private job" if is_private else "Scheduled command"
            meta["schedulerId"] = identifier
        else:
            identifier = "automation-cron-" + slug(label)
            name = label
            meta["label"] = truncate(label, 200)
        items.append(make_item(
            context, identifier, name, "automation",
            "Scheduled command registered in the user crontab.",
            status="configured", status_label="Present in the crontab. The command text is not collected.",
            tags=["cron", "calendar"], runtimes=["cron"],
            evidence=[context.evidence("crontab -l", "Schedule expression read; the command is not published")],
            schedule=schedule, meta=meta,
        ))
    return items


def collect_systemd(context: Context) -> list[dict[str, Any]]:
    """User systemd timers, when the host provides them."""
    if platform.system() != "Linux":
        return []
    ok, output = run_command(["systemctl", "--user", "list-timers", "--all", "--no-pager"], timeout=20)
    if not ok:
        return []
    include, exclude, private = job_patterns(context)
    items: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = re.search(r"(\S+\.timer)", line)
        if not match:
            continue
        label = match.group(1)
        if not label_selected(label, include, exclude):
            continue
        is_private = any(fnmatch.fnmatch(label, pattern) for pattern in private)
        if is_private:
            context.private_labels.append(label)
        meta: dict[str, Any] = {}
        if is_private:
            identifier, name = opaque_job_id(label), "Private job"
            meta["schedulerId"] = identifier
        else:
            identifier, name = "automation-" + slug(label), label
            meta["label"] = truncate(label, 200)
        items.append(make_item(
            context, identifier, name, "automation",
            "Timer registered with the user systemd instance.",
            status="observed", status_label="Listed by the user timer scheduler.",
            tags=["systemd", "timer"], runtimes=["systemd"],
            evidence=[context.evidence("systemctl --user list-timers", "Timer unit listed by the scheduler")],
            schedule={"kind": "other", "label": "Runs on a systemd timer", "loaded": True}, meta=meta,
        ))
    return items


def collect_automation(context: Context) -> list[dict[str, Any]]:
    """Scheduled jobs from every scheduler available on this machine."""
    return collect_launchd(context) + collect_cron(context) + collect_systemd(context)


COLLECTORS = {
    "runtime": collect_runtime,
    "skills": collect_skills,
    "agents": collect_agents,
    "harness": collect_harness,
    "automation": collect_automation,
}

MODULE_SCOPE = {
    "runtime": "Models, command line tools and tool connections",
    "skills": "Skill definitions found in local skill roots",
    "agents": "Agent definitions found in local agent roots",
    "harness": "Hooks, rules and instruction files",
    "automation": "Scheduled jobs registered with the local scheduler",
}


def collect_module(context: Context, module: str) -> dict[str, Any]:
    """Run one module and return its part document."""
    collector = COLLECTORS[module]
    items = collector(context)
    return {
        "module": module,
        "collectedAt": context.checked_at,
        "machine": context.machine,
        "scope": MODULE_SCOPE[module],
        "items": items[:MAX_ITEMS],
        "privateLabels": list(context.private_labels),
    }


# --------------------------------------------------------------------------- #
# Machine records and merging
# --------------------------------------------------------------------------- #

def operating_system() -> str:
    """Human readable operating system name and version."""
    system = platform.system()
    if system == "Darwin":
        return ("macOS " + platform.mac_ver()[0]).strip()
    if system == "Linux":
        for line in read_text(Path("/etc/os-release"), 8_192).splitlines():
            if line.startswith("PRETTY_NAME="):
                return truncate(line.split("=", 1)[1].strip().strip('"'), 120)
        return ("Linux " + platform.release()).strip()
    return (system + " " + platform.release()).strip()


def detected_runtimes(items: Sequence[dict[str, Any]]) -> list[str]:
    """Agent runtimes whose command line tool was found on this machine."""
    known = ("claude", "codex", "gemini", "grok", "goose", "aider", "cursor")
    found = {item["name"] for item in items if item["category"] == "cli"}
    return [name for name in known if name in found]


def machine_record(context: Context, items: Sequence[dict[str, Any]], modules: Sequence[str], label: str = "") -> dict[str, Any]:
    """Summary record for the machine this scan ran on."""
    record: dict[str, Any] = {
        "id": context.machine,
        "status": "observed",
        "statusLabel": truncate(label or "Scanned locally, read only.", 500),
        "checkedAt": context.checked_at,
        "architecture": platform.machine(),
        "os": operating_system(),
    }
    if "automation" in modules:
        jobs = [item for item in items if item["category"] == "automation"]
        record["jobCount"] = len(jobs)
        record["loadedJobCount"] = sum(1 for item in jobs if item.get("schedule", {}).get("loaded"))
        record["runningJobCount"] = sum(1 for item in jobs if item.get("schedule", {}).get("running"))
    if "skills" in modules:
        record["skillCount"] = sum(1 for item in items if item["category"] == "skill")
    if "runtime" in modules:
        record["runtimes"] = detected_runtimes(items)
    return record


def merge_remote_items(items: list[dict[str, Any]], remote_items: Sequence[dict[str, Any]], machine: str) -> int:
    """Fold a remote machine's items into the local list, keeping ids unique.

    An item that already exists with the same category and name is recorded as
    also present on the remote machine.  A genuine id collision between unlike
    items is given a machine-suffixed id instead.
    """
    index = {item["id"]: item for item in items}
    added = 0
    for remote in remote_items:
        existing = index.get(remote["id"])
        if existing and existing["category"] == remote["category"] and existing["name"] == remote["name"]:
            if machine not in existing["machines"]:
                existing["machines"].append(machine)
            for evidence in remote.get("evidence", []):
                if len(existing["evidence"]) < MAX_EVIDENCE:
                    existing["evidence"].append(evidence)
            continue
        copy = dict(remote)
        if existing:
            copy["id"] = (remote["id"] + "-" + machine)[:160]
        copy["machines"] = [machine]
        items.append(copy)
        index[copy["id"]] = copy
        added += 1
    return added


def remote_scan(machine: dict[str, Any], script: str, timeout: float = REMOTE_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    """Run this scanner on a remote host over SSH and parse its output."""
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", str(machine["host"]),
        "python3", "-", "--stdout", "--machine", str(machine["id"]), "--modules", ",".join(MODULES),
    ]
    ok, output = run_command(command, timeout=timeout, stdin_text=script)
    if not ok:
        return None
    start = output.find("{")
    if start < 0:
        return None
    try:
        parsed = json.loads(output[start:])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and isinstance(parsed.get("items"), list) else None


def unavailable_machine(machine: dict[str, Any], previous: dict[str, Any] | None, checked_at: str) -> dict[str, Any]:
    """Machine record kept from the last successful scan, marked stale."""
    record = dict(previous or {})
    record.update({
        "id": str(machine["id"]),
        "status": "unavailable",
        "statusLabel": "The machine could not be reached during this scan.",
        "reachability": "unreachable",
        "stale": True,
        "lastAttemptAt": checked_at,
    })
    if previous and isinstance(previous.get("checkedAt"), str):
        record["lastSuccessfulAt"] = previous["checkedAt"]
    record.setdefault("checkedAt", checked_at)
    return record


def build_inventory(
    context: Context,
    parts: dict[str, dict[str, Any]],
    remote_machines: dict[str, dict[str, Any]] | None = None,
    local_label: str = "",
    remote_items: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Merge module parts, and any remote machine's items, into one inventory."""
    modules = [module for module in MODULES if module in parts]
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for module in modules:
        part = parts[module]
        part_items = [dict(item) for item in part.get("items", [])]
        items.extend(part_items)
        sources.append({
            "name": module,
            "collectedAt": part.get("collectedAt", context.checked_at),
            "count": len(part_items),
            "scope": MODULE_SCOPE.get(module, module),
        })

    local_items = list(items)
    machines: dict[str, dict[str, Any]] = {context.machine: machine_record(context, local_items, modules, local_label)}
    for machine_id, record in (remote_machines or {}).items():
        machines[machine_id] = record

    for machine_id, machine_items in (remote_items or {}).items():
        if machines.get(machine_id, {}).get("status") == "unavailable":
            continue
        added = merge_remote_items(items, machine_items, machine_id)
        sources.append({
            "name": machine_id,
            "collectedAt": machines.get(machine_id, {}).get("checkedAt", context.checked_at),
            "count": added,
            "scope": "Items found on a remote machine",
        })

    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        if item["id"] in seen:
            raise ValueError("duplicate inventory id: " + item["id"])
        seen[item["id"]] = item
    for item in items:
        related = [identifier for identifier in item.get("relatedIds", []) if identifier in seen]
        if related:
            item["relatedIds"] = related
        else:
            item.pop("relatedIds", None)

    notes = [
        NOTES["runtime"] if "runtime" in modules else "",
        NOTES["connection"] if "runtime" in modules else "",
        NOTES["skills"] if "skills" in modules or "agents" in modules else "",
        NOTES["automation"] if "automation" in modules else "",
        NOTES["privacy"],
    ]
    private_count = sum(1 for item in items if item["id"].startswith("automation-private-"))
    if private_count:
        notes.append("Jobs matching a private pattern are published with an opaque id and a generic name: " + str(private_count) + " job(s).")
    unreachable = [machine_id for machine_id, record in machines.items() if record.get("status") == "unavailable"]
    if unreachable:
        notes.append("These machines could not be reached during this scan, so their records are from an earlier run: " + ", ".join(sorted(unreachable)) + ".")
    if len(modules) < len(MODULES):
        notes.append("This scan ran a subset of the collector modules: " + ", ".join(modules) + ".")

    items.sort(key=lambda item: (CATEGORY_ORDER.index(item["category"]) if item["category"] in CATEGORY_ORDER else len(CATEGORY_ORDER), item["name"].casefold(), item["id"]))
    inventory = {
        "collectedAt": context.checked_at,
        "items": items[:MAX_ITEMS],
        "sources": sources[:20],
        "machines": machines,
        "notes": [note for note in dict.fromkeys(notes) if note][:100],
    }
    return sanitize(inventory, context.home)


def run_scan(
    config: dict[str, Any],
    machine_id: str,
    modules: Sequence[str],
    *,
    remote: bool = True,
    previous: dict[str, Any] | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Collect the requested modules and return a complete inventory."""
    context = Context(config, machine_id, checked_at)
    parts = {module: collect_module(context, module) for module in modules}
    local_label = ""
    for machine in config.get("machines", []) if isinstance(config.get("machines"), list) else []:
        if isinstance(machine, dict) and machine.get("id") == machine_id and isinstance(machine.get("label"), str):
            local_label = "Scanned locally, read only: " + machine["label"]
    remote_records: dict[str, dict[str, Any]] = {}
    remote_parts: dict[str, list[dict[str, Any]]] = {}
    if remote:
        script_path = Path(__file__).resolve() if "__file__" in globals() else None
        source = read_text(script_path, 1_000_000) if script_path else ""
        previous_machines = (previous or {}).get("machines", {}) if isinstance(previous, dict) else {}
        for machine in config.get("machines", []) if isinstance(config.get("machines"), list) else []:
            if not isinstance(machine, dict) or machine.get("kind") != "ssh" or not machine.get("host") or not machine.get("id"):
                continue
            payload = remote_scan(machine, source) if source else None
            if payload is None:
                remote_records[str(machine["id"])] = unavailable_machine(machine, previous_machines.get(str(machine["id"])), context.checked_at)
                continue
            remote_parts[str(machine["id"])] = [item for item in payload.get("items", []) if isinstance(item, dict)]
            record = payload.get("machines", {}).get(str(machine["id"]))
            remote_records[str(machine["id"])] = record if isinstance(record, dict) else {"id": str(machine["id"]), "status": "unknown", "checkedAt": context.checked_at}
    inventory = build_inventory(context, parts, remote_records, local_label, remote_parts)
    rendered = json.dumps(inventory, ensure_ascii=False)
    for label in context.private_labels:
        if label and label in rendered:
            raise SystemExit("refused to publish: a private job label reached the output")
    return inventory


def parse_modules(value: str) -> list[str]:
    """Validate and order a comma separated module list."""
    requested = [entry.strip() for entry in value.split(",") if entry.strip()]
    unknown = [entry for entry in requested if entry not in MODULES]
    if unknown:
        raise argparse.ArgumentTypeError("unknown module(s): " + ", ".join(unknown))
    return [module for module in MODULES if module in requested]


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Scan this machine's AI environment, read only.")
    parser.add_argument("--out", help="write the inventory to this path (default: data/inventory.json)")
    parser.add_argument("--stdout", action="store_true", help="print the inventory instead of writing a file")
    parser.add_argument("--machine", help="machine id to tag the scan with (used for remote runs)")
    parser.add_argument("--modules", type=parse_modules, default=list(MODULES), help="comma separated module list")
    parser.add_argument("--config", help="path to a guild configuration file")
    parser.add_argument("--no-remote", action="store_true", help="skip configured remote machines")
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    config = load_config(arguments.config)
    machine_id = arguments.machine
    if not machine_id:
        for machine in config.get("machines", []) if isinstance(config.get("machines"), list) else []:
            if isinstance(machine, dict) and machine.get("kind") == "local" and isinstance(machine.get("id"), str):
                machine_id = machine["id"]
                break
    if not machine_id:
        machine_id = "main"
    if not re.match(r"^[a-z0-9-]{1,32}$", machine_id):
        print("invalid machine id: " + machine_id, file=sys.stderr)
        return 2

    root = repo_root()
    default_out = (root / "data" / "inventory.json") if root else Path("data/inventory.json")
    out_path = Path(arguments.out).expanduser() if arguments.out else default_out
    previous = read_json(out_path, None) if out_path.is_file() else None
    remote = not arguments.no_remote and not arguments.machine

    inventory = run_scan(config, machine_id, arguments.modules, remote=remote, previous=previous)
    rendered = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if arguments.stdout:
        sys.stdout.write(rendered)
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, out_path)
    counts: dict[str, int] = {}
    for item in inventory["items"]:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print("wrote " + display_path(out_path) + ": " + str(len(inventory["items"])) + " items " + json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
