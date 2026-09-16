"""Tests for the publish loop: refresh, upload, heartbeat and backoff."""

from __future__ import annotations

import contextlib
import io
import json
import time
import unittest
from pathlib import Path
from typing import Any

from collector import sync
from collector.tests.support import HomeTestCase, StubServer, write, write_json


class FakeSite:
    """Minimal stand-in for the guild worker."""

    def __init__(self) -> None:
        self.revision = 0
        self.content_hash = ""
        self.force_status = 0
        self.stats_calls = 0
        self.inventory_calls = 0
        self.heartbeat_calls = 0

    def handle(self, path: str, method: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, Any]]:
        """Answer one request the way the real endpoints do."""
        if self.force_status:
            status, self.force_status = self.force_status, 0
            return status, {"error": "forced", "currentRevision": self.revision or None}
        if path == "/api/inventory":
            self.inventory_calls += 1
            payload = json.loads(body.decode("utf-8"))
            if payload["revision"] <= self.revision:
                return 409, {"error": "out_of_order_revision", "currentRevision": self.revision}
            self.revision = payload["revision"]
            self.content_hash = "a" * 64
            return 201, {"accepted": True, "sync": {"revision": self.revision, "contentHash": self.content_hash}}
        if path == "/api/inventory/heartbeat":
            self.heartbeat_calls += 1
            payload = json.loads(body.decode("utf-8"))
            if payload["revision"] != self.revision or payload["contentHash"] != self.content_hash:
                return 409, {"error": "out_of_order_revision", "currentRevision": self.revision}
            return 200, {"accepted": True, "sync": {"revision": self.revision, "contentHash": self.content_hash}}
        if path == "/api/stats":
            self.stats_calls += 1
            return 202, {"accepted": True}
        return 404, {"error": "not_found"}


class SyncTestCase(HomeTestCase):
    """Shared setup: a fixture machine, a config file and a stub site."""

    def setUp(self) -> None:
        super().setUp()
        write(self.home / ".claude/CLAUDE.md", "instructions")
        write(self.home / ".claude/rules/review.md", "# Rules\n\nReview every change.\n")
        write_json(self.home / ".claude/settings.json", {"hooks": {"Stop": [{"one": 1}]}})
        write(self.home / ".agents/skills/release-notes/SKILL.md", "---\nname: release-notes\ndescription: Turn merged work into notes.\n---\n")
        write(self.home / ".claude/agents/compass.md", "---\nname: compass\ndescription: Architecture reviews, migration plans\n---\n")
        self.config_path = write_json(self.home / "guild.config.json", self.config())
        self.secret_path = self.home / "ingest.secret"
        self.secret_path.write_text("this-is-a-long-enough-secret", encoding="utf-8")
        self.secret_path.chmod(0o600)
        self.data_dir = self.home / "data"
        self.state_path = self.home / "state/sync-state.json"
        self.log_path = self.home / "state/sync.log"
        self.site = FakeSite()

    def write_sync_config(self, origin: str, secret_file: Path | None = None, path: str = "/api/inventory") -> Path:
        """Write the sync configuration that points at the stub site."""
        return write_json(self.home / "sync.json", {
            "endpoint": origin + path,
            "ingestSecretFile": str(secret_file or self.secret_path),
        })

    def run_sync(self, extra: list[str] | None = None) -> str:
        """Run one sync cycle and return its printed output."""
        arguments = [
            "--once",
            "--config", str(self.config_path),
            "--sync-config", str(self.home / "sync.json"),
            "--state", str(self.state_path),
            "--usage-state", str(self.home / "state/usage.json"),
            "--usage-root", str(self.home),
            "--data-dir", str(self.data_dir),
            "--log", str(self.log_path),
            "--allow-http",
        ]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            sync.main(arguments + (extra or []))
        return buffer.getvalue()

    def state(self) -> dict[str, Any]:
        """The persisted sync state."""
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class LocalRefreshTest(SyncTestCase):
    """Building the inventory without any network configured."""

    def test_a_cycle_without_a_destination_still_builds_the_inventory(self) -> None:
        output = self.run_sync()
        inventory = json.loads((self.data_dir / "inventory.json").read_text(encoding="utf-8"))
        self.assertTrue(inventory["items"])
        self.assertIn("blocked", output)
        self.assertTrue((self.data_dir / "parts/skills.json").is_file())

    def test_statistics_are_written_even_when_offline(self) -> None:
        self.run_sync()
        stats = json.loads((self.data_dir / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual(7, len(stats["days"]))
        self.assertEqual("local-only", self.state()["statsStatus"])

    def test_the_log_stays_free_of_credentials(self) -> None:
        self.run_sync()
        contents = self.log_path.read_text(encoding="utf-8")
        self.assertIn("cycle changed=true", contents)
        self.assertNotIn("Bearer", contents)
        self.assertNotIn("this-is-a-long-enough-secret", contents)


class ConfigurationTest(SyncTestCase):
    """Refusals that happen before anything is sent."""

    def check(self, extra: list[str]) -> str:
        """Run --check and return its output."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            sync.main(["--check", "--config", str(self.config_path), "--sync-config", str(self.home / "sync.json"), "--state", str(self.state_path)] + extra)
        return buffer.getvalue().strip()

    def test_a_readable_secret_file_is_refused(self) -> None:
        self.write_sync_config("https://guild.example")
        self.secret_path.chmod(0o644)
        self.assertIn("chmod 600", self.check([]))

    def test_plain_http_is_refused_by_default(self) -> None:
        self.write_sync_config("http://127.0.0.1:1")
        self.assertIn("https", self.check([]))

    def test_plain_http_is_allowed_for_local_tests(self) -> None:
        self.write_sync_config("http://127.0.0.1:1")
        self.assertEqual("ready", self.check(["--allow-http"]))

    def test_the_endpoint_path_is_fixed(self) -> None:
        self.write_sync_config("https://guild.example", path="/api/upload")
        self.assertIn("/api/inventory", self.check([]))

    def test_a_missing_configuration_is_reported(self) -> None:
        self.assertIn("missing", self.check([]))


class PublishTest(SyncTestCase):
    """Uploading, heartbeating and recovering."""

    def test_the_first_cycle_uploads_revision_one(self) -> None:
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            self.assertIn("/api/inventory", server.paths())
        state = self.state()
        self.assertEqual(1, state["revision"])
        self.assertEqual("synced", state["syncStatus"])
        self.assertEqual(1, self.site.inventory_calls)
        self.assertEqual(1, self.site.heartbeat_calls)
        self.assertEqual(1, self.site.stats_calls)

    def test_an_unchanged_inventory_only_heartbeats(self) -> None:
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            self.run_sync()
        self.assertEqual(1, self.site.inventory_calls)
        self.assertEqual(2, self.site.heartbeat_calls)
        self.assertEqual(1, self.state()["revision"])

    def test_a_changed_machine_uploads_a_new_revision(self) -> None:
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            write(self.home / ".agents/skills/new-skill/SKILL.md", "---\nname: new-skill\ndescription: Something new.\n---\n")
            self.run_sync()
        self.assertEqual(2, self.site.inventory_calls)
        self.assertEqual(2, self.state()["revision"])

    def test_the_upload_carries_the_collector_envelope(self) -> None:
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            envelope = json.loads(next(entry["body"] for entry in server.requests if entry["path"] == "/api/inventory").decode("utf-8"))
        self.assertEqual("collector", envelope["source"])
        self.assertEqual(1, envelope["schemaVersion"])
        self.assertGreaterEqual(envelope["heartbeatAt"], envelope["collectedAt"])
        self.assertIn("items", envelope["inventory"])

    def test_a_conflict_rebases_on_the_server_revision(self) -> None:
        self.site.revision = 7
        self.site.content_hash = "b" * 64
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            self.assertEqual("pending-newer", self.state()["syncStatus"])
            self.assertEqual(7, self.state()["revision"])
            self.run_sync()
        self.assertEqual(8, self.state()["revision"])
        self.assertEqual("synced", self.state()["syncStatus"])

    def test_a_server_error_retries_the_same_body_later(self) -> None:
        self.site.force_status = 503
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            state = self.state()
            self.assertEqual("retry", state["syncStatus"])
            self.assertGreater(state["retryAfter"], time.time())
            self.assertIn("pendingEnvelope", state)
            self.assertEqual(1, state["pendingEnvelope"]["body"]["revision"])
            state["retryAfter"] = 0
            self.state_path.write_text(json.dumps(state), encoding="utf-8")
            self.run_sync()
        self.assertEqual("synced", self.state()["syncStatus"])
        self.assertEqual(1, self.state()["revision"])

    def test_a_rejected_credential_blocks_instead_of_retrying(self) -> None:
        self.site.force_status = 401
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
        state = self.state()
        self.assertEqual("blocked", state["syncStatus"])
        self.assertIn("credential", state["syncDetail"])

    def test_changing_the_destination_forgets_the_old_revision(self) -> None:
        with StubServer(self.site.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
            self.assertEqual(1, self.state()["revision"])
        other = FakeSite()
        with StubServer(other.handle) as server:
            self.write_sync_config(server.origin)
            self.run_sync()
        self.assertEqual(1, other.inventory_calls)
        self.assertEqual(1, self.state()["revision"])


class FingerprintTest(HomeTestCase):
    """The change detector reads metadata only."""

    def test_content_changes_are_detected(self) -> None:
        path = write(self.home / "watched/file.txt", "one")
        before = sync.fingerprint([self.home / "watched"])
        path.write_text("two longer text", encoding="utf-8")
        self.assertNotEqual(before, sync.fingerprint([self.home / "watched"]))

    def test_missing_paths_are_ignored(self) -> None:
        self.assertEqual(sync.fingerprint([self.home / "nope"]), sync.fingerprint([]))

    def test_freshness_fields_do_not_change_the_digest(self) -> None:
        first = {"collectedAt": "2026-01-01T00:00:00Z", "items": [{"id": "a", "evidence": [{"checkedAt": "2026-01-01T00:00:00Z"}]}]}
        second = {"collectedAt": "2026-02-02T00:00:00Z", "items": [{"id": "a", "evidence": [{"checkedAt": "2026-02-02T00:00:00Z"}]}]}
        self.assertEqual(sync.semantic_digest(first), sync.semantic_digest(second))

    def test_real_changes_do_change_the_digest(self) -> None:
        first = {"items": [{"id": "a"}]}
        second = {"items": [{"id": "b"}]}
        self.assertNotEqual(sync.semantic_digest(first), sync.semantic_digest(second))


if __name__ == "__main__":
    unittest.main()
