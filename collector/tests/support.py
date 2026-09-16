"""Shared fixtures for the collector tests: a temporary home and a stub server."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


def write(path: Path, text: str) -> Path:
    """Create parent folders and write a text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, value: Any) -> Path:
    """Create parent folders and write a JSON file."""
    return write(path, json.dumps(value, ensure_ascii=False, indent=2))


class HomeTestCase(unittest.TestCase):
    """Base case that runs each test inside a throwaway home directory."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="ai-guild-test-"))
        self._previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        self.addCleanup(shutil.rmtree, self.home, True)

    def _restore_home(self) -> None:
        if self._previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._previous_home

    def config(self, **overrides: Any) -> dict[str, Any]:
        """A minimal guild configuration, merged with the given overrides."""
        base: dict[str, Any] = {
            "timezone": "UTC",
            "machines": [{"id": "main", "label": "This machine", "kind": "local"}],
            "theme": {"preset": "test-preset"},
            "roles": {},
            "art": {
                "style": "flat test style",
                "characterConcept": "test companions",
                "subjects": ["owl analyst with a telescope", "otter coordinator with a lantern"],
                "toolStyle": "flat test object",
                "constraints": "no text",
            },
            "collector": {
                "roots": {"claude": "~/.claude", "codex": "~/.codex", "sharedSkills": "~/.agents/skills"},
                "projectRoots": ["~/Projects"],
                "clis": [],
                "jobs": {"includePatterns": ["*"], "excludePatterns": ["com.apple.*"], "privatePatterns": []},
                "usage": {"providers": ["codex", "claude"]},
            },
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merged = dict(base[key])
                for inner_key, inner_value in value.items():
                    if isinstance(inner_value, dict) and isinstance(merged.get(inner_key), dict):
                        merged[inner_key] = dict(merged[inner_key], **inner_value)
                    else:
                        merged[inner_key] = inner_value
                base[key] = merged
            else:
                base[key] = value
        return base


class StubServer:
    """A tiny HTTP server that records the requests a collector sends it."""

    def __init__(self, handler: Callable[[str, str, dict[str, str], bytes], tuple[int, dict[str, Any]]]) -> None:
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - http.server contract
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length)
                headers = {key.lower(): value for key, value in self.headers.items()}
                status, reply = handler(self.path, self.command, headers, body)
                outer.requests.append({"path": self.path, "headers": headers, "body": body, "status": status})
                payload = json.dumps(reply).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:  # keep test output clean
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubServer":
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def origin(self) -> str:
        """Base URL of the running stub server."""
        host, port = self.server.server_address[:2]
        return "http://" + str(host) + ":" + str(port)

    def paths(self) -> list[str]:
        """Paths of every request received so far."""
        return [entry["path"] for entry in self.requests]
