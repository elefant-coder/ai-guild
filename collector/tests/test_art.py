"""Tests for the artwork pipeline: plan, import, push and status."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from collector import art
from collector.tests.support import HomeTestCase, StubServer, write_json

# A 1x1 transparent PNG, used where a real encoder is not available.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def inventory_document(summary: str = "Maps a change across the whole system.") -> dict[str, Any]:
    """A tiny inventory with one agent and one skill."""
    checked = "2026-01-01T00:00:00Z"
    evidence = [{"source": "~/.claude/agents/compass.md", "detail": "read", "checkedAt": checked}]
    return {
        "collectedAt": checked,
        "items": [
            {"id": "agent-compass", "name": "compass", "category": "agent", "summary": summary,
             "tags": ["agent"], "status": "configured", "statusLabel": "found", "machines": ["main"],
             "runtimes": ["claude"], "evidence": evidence,
             "meta": {"strengths": ["Architecture"], "exampleTask": "Plan the split."}},
            {"id": "skill-video-cutdown", "name": "video-cutdown", "category": "skill",
             "summary": "Cut a long recording into short clips with captions.", "tags": ["shared"],
             "status": "configured", "statusLabel": "found", "machines": ["main"], "runtimes": ["shared"],
             "evidence": evidence},
        ],
        "sources": [{"name": "runtime", "collectedAt": checked, "count": 2}],
        "machines": {"main": {"id": "main", "status": "observed", "checkedAt": checked}},
        "notes": [],
    }


class ArtTestCase(HomeTestCase):
    """Shared fixture paths for the art commands."""

    def setUp(self) -> None:
        super().setUp()
        self.config_path = write_json(self.home / "guild.config.json", self.config())
        self.inventory_path = write_json(self.home / "inventory.json", inventory_document())
        self.plan_path = self.home / "art-plan.json"
        self.manifest_path = self.home / "characters.json"
        self.art_dir = self.home / "art"

    def run_art(self, arguments: list[str]) -> str:
        """Run one art command and return its printed output."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            art.main(["--config", str(self.config_path)] + arguments)
        return buffer.getvalue()

    def make_plan(self, extra: list[str] | None = None) -> dict[str, Any]:
        """Write a plan and return it."""
        self.run_art(["plan", "--inventory", str(self.inventory_path), "--out", str(self.plan_path)] + (extra or []))
        return json.loads(self.plan_path.read_text(encoding="utf-8"))


class PlanTest(ArtTestCase):
    """Turning an inventory into image jobs."""

    def test_only_agents_are_planned_by_default(self) -> None:
        plan = self.make_plan()
        self.assertEqual(["agent-compass"], [job["id"] for job in plan["jobs"]])

    def test_tools_can_be_included(self) -> None:
        plan = self.make_plan(["--include-tools"])
        self.assertEqual({"agent-compass", "skill-video-cutdown"}, {job["id"] for job in plan["jobs"]})

    def test_the_prompt_version_follows_the_theme(self) -> None:
        self.assertEqual("test-preset-v1", self.make_plan()["promptVersion"])

    def test_the_descriptor_hash_is_stable(self) -> None:
        first = self.make_plan()["jobs"][0]["descriptorHash"]
        second = self.make_plan()["jobs"][0]["descriptorHash"]
        self.assertEqual(first, second)

    def test_a_changed_summary_changes_the_hash(self) -> None:
        before = self.make_plan()["jobs"][0]["descriptorHash"]
        write_json(self.inventory_path, inventory_document("A completely different role."))
        self.assertNotEqual(before, self.make_plan()["jobs"][0]["descriptorHash"])

    def test_the_subject_choice_is_stable_for_an_id(self) -> None:
        subjects = ["one", "two", "three"]
        self.assertEqual(art.stable_choice("agent-compass", subjects), art.stable_choice("agent-compass", subjects))
        self.assertIn(art.stable_choice("agent-compass", subjects), subjects)

    def test_the_character_prompt_uses_the_configured_style(self) -> None:
        prompt = self.make_plan()["jobs"][0]["prompt"]
        self.assertIn("flat test style", prompt)
        self.assertIn("test companions", prompt)
        self.assertIn("agent-compass", prompt)
        self.assertIn("no text", prompt)

    def test_tool_prompts_pick_an_object_family(self) -> None:
        jobs = {job["id"]: job for job in self.make_plan(["--include-tools"])["jobs"]}
        self.assertIn("clapperboard", jobs["skill-video-cutdown"]["prompt"])
        self.assertIn("flat test object", jobs["skill-video-cutdown"]["prompt"])

    def test_unmatched_tools_fall_back_to_a_generic_object(self) -> None:
        self.assertEqual(art.DEFAULT_TOOL, art.tool_object("quiet-helper", "Does something unusual.", "skill"))


class ImportTest(ArtTestCase):
    """Filing an image and recording it in the manifest."""

    def png(self, width: int = 900, height: int = 600) -> Path:
        """Write a PNG of the requested size, or the tiny fallback."""
        path = self.home / "source.png"
        if art.Image is None:
            path.write_bytes(TINY_PNG)
            return path
        art.Image.new("RGB", (width, height), (120, 90, 200)).save(path, "PNG")
        return path

    def import_image(self, path: Path, identifier: str = "agent-compass") -> dict[str, Any]:
        """Import one image and return the manifest."""
        self.make_plan(["--include-tools"])
        self.run_art([
            "import", "--id", identifier, "--file", str(path), "--plan", str(self.plan_path),
            "--inventory", str(self.inventory_path), "--art-dir", str(self.art_dir), "--manifest", str(self.manifest_path),
        ])
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    @unittest.skipIf(art.Image is None, "Pillow is not installed")
    def test_the_longest_side_becomes_512(self) -> None:
        self.import_image(self.png())
        with art.Image.open(self.art_dir / "agent-compass.webp") as image:
            self.assertEqual(512, max(image.width, image.height))
            self.assertEqual("WEBP", image.format)

    @unittest.skipIf(art.Image is None, "Pillow is not installed")
    def test_the_stored_image_stays_under_the_limit(self) -> None:
        self.import_image(self.png(2000, 2000))
        self.assertLessEqual((self.art_dir / "agent-compass.webp").stat().st_size, art.MAX_IMAGE_BYTES)

    def test_the_manifest_records_the_descriptor_hash(self) -> None:
        manifest = self.import_image(self.png())
        entry = manifest["characters"]["agent-compass"]
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual("ready", entry["status"])
        self.assertEqual(plan["jobs"][0]["descriptorHash"], entry["contentHash"])
        self.assertEqual("test-preset-v1", entry["promptVersion"])
        self.assertTrue(entry["imageUrl"].startswith("/art/characters/agent-compass."))

    def test_a_file_that_is_not_an_image_is_refused(self) -> None:
        bad = self.home / "notes.txt"
        bad.write_text("not an image", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.import_image(bad)

    def test_an_unknown_id_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.import_image(self.png(), identifier="agent-nobody")

    def test_without_pillow_a_small_file_is_copied(self) -> None:
        source = self.home / "small.png"
        source.write_bytes(TINY_PNG)
        with mock.patch.object(art, "Image", None):
            manifest = self.import_image(source)
        self.assertEqual("/art/characters/agent-compass.png", manifest["characters"]["agent-compass"]["imageUrl"])
        self.assertEqual(TINY_PNG, (self.art_dir / "agent-compass.png").read_bytes())

    def test_without_pillow_a_large_file_is_refused(self) -> None:
        source = self.home / "large.png"
        source.write_bytes(TINY_PNG + b"\x00" * art.MAX_IMAGE_BYTES)
        with mock.patch.object(art, "Image", None), self.assertRaises(SystemExit):
            self.import_image(source)


class FakeCharacterSite:
    """Minimal stand-in for the character endpoints."""

    def __init__(self) -> None:
        self.registered: dict[str, str] = {}
        self.images: dict[str, bytes] = {}
        self.descriptors: list[str] = []

    def handle(self, path: str, method: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, Any]]:
        """Answer a register or image upload request."""
        if path == "/api/characters":
            payload = json.loads(body.decode("utf-8"))
            character = payload["characters"][0]
            unchanged = self.registered.get(character["id"]) == character["contentHash"]
            self.registered[character["id"]] = character["contentHash"]
            return 202, {"accepted": True, "queued": 0 if unchanged else 1, "unchanged": 1 if unchanged else 0}
        if path.endswith("/image"):
            identifier = path.split("/")[-2]
            descriptor = headers.get("x-character-descriptor", "")
            self.descriptors.append(descriptor)
            if self.registered.get(identifier) != descriptor:
                return 409, {"error": "descriptor_mismatch"}
            self.images[identifier] = body
            return 201, {"accepted": True, "id": identifier, "status": "ready"}
        return 404, {"error": "not_found"}


class PushTest(ArtTestCase):
    """Uploading stored portraits."""

    def setUp(self) -> None:
        super().setUp()
        self.secret_path = self.home / "ingest.secret"
        self.secret_path.write_text("this-is-a-long-enough-secret", encoding="utf-8")
        self.secret_path.chmod(0o600)
        self.state_path = self.home / "art-push.json"
        self.site = FakeCharacterSite()
        source = self.home / "source.png"
        if art.Image is None:
            source.write_bytes(TINY_PNG)
        else:
            art.Image.new("RGB", (600, 400), (30, 40, 90)).save(source, "PNG")
        self.make_plan()
        self.run_art([
            "import", "--id", "agent-compass", "--file", str(source), "--plan", str(self.plan_path),
            "--inventory", str(self.inventory_path), "--art-dir", str(self.art_dir), "--manifest", str(self.manifest_path),
        ])

    def push(self, origin: str) -> str:
        """Run push against a stub site."""
        write_json(self.home / "sync.json", {"endpoint": origin + "/api/inventory", "ingestSecretFile": str(self.secret_path)})
        return self.run_art([
            "push", "--inventory", str(self.inventory_path), "--manifest", str(self.manifest_path),
            "--art-dir", str(self.art_dir), "--sync-config", str(self.home / "sync.json"), "--state", str(self.state_path),
        ])

    def test_a_portrait_is_registered_then_uploaded(self) -> None:
        with StubServer(self.site.handle) as server:
            output = self.push(server.origin)
        self.assertIn("pushed 1", output)
        self.assertIn("agent-compass", self.site.images)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["characters"]["agent-compass"]["contentHash"], self.site.descriptors[0])

    def test_an_unchanged_portrait_is_skipped_next_time(self) -> None:
        with StubServer(self.site.handle) as server:
            self.push(server.origin)
            output = self.push(server.origin)
        self.assertIn("skipped 1", output)
        self.assertEqual(1, len(self.site.descriptors))

    def test_a_new_image_is_uploaded_again(self) -> None:
        with StubServer(self.site.handle) as server:
            self.push(server.origin)
            replacement = self.home / "replacement.png"
            if art.Image is None:
                replacement.write_bytes(TINY_PNG + b"")
                self.art_dir.joinpath("agent-compass.png").write_bytes(TINY_PNG)
            else:
                art.Image.new("RGB", (600, 400), (200, 10, 10)).save(replacement, "PNG")
                self.run_art([
                    "import", "--id", "agent-compass", "--file", str(replacement), "--plan", str(self.plan_path),
                    "--inventory", str(self.inventory_path), "--art-dir", str(self.art_dir), "--manifest", str(self.manifest_path),
                ])
            output = self.push(server.origin)
        if art.Image is not None:
            self.assertIn("pushed 1", output)
            self.assertEqual(2, len(self.site.descriptors))

    def test_an_entry_outside_the_inventory_is_skipped(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["characters"]["agent-ghost"] = dict(manifest["characters"]["agent-compass"], imageUrl="/art/characters/agent-ghost.webp")
        write_json(self.manifest_path, manifest)
        with StubServer(self.site.handle) as server:
            output = self.push(server.origin)
        self.assertIn("not in the current inventory", output)

    def test_a_world_readable_secret_is_refused(self) -> None:
        self.secret_path.chmod(0o644)
        with StubServer(self.site.handle) as server, self.assertRaises(SystemExit):
            self.push(server.origin)

    def test_the_push_state_file_is_private(self) -> None:
        with StubServer(self.site.handle) as server:
            self.push(server.origin)
        self.assertEqual(0o600, self.state_path.stat().st_mode & 0o777)


class StatusTest(ArtTestCase):
    """The readiness table."""

    def test_missing_entries_are_listed(self) -> None:
        output = self.run_art(["status", "--inventory", str(self.inventory_path), "--manifest", str(self.manifest_path)])
        self.assertIn("agent-compass", output)
        self.assertIn("missing", output)
        self.assertIn("0 ready of 1", output)

    def test_a_stale_hash_is_reported(self) -> None:
        write_json(self.manifest_path, {"schemaVersion": 1, "characters": {
            "agent-compass": {"status": "ready", "imageUrl": "/art/characters/agent-compass.webp", "updatedAt": "2026-01-01T00:00:00Z", "promptVersion": "test-preset-v1", "contentHash": "0" * 64},
        }})
        output = self.run_art(["status", "--inventory", str(self.inventory_path), "--manifest", str(self.manifest_path)])
        self.assertIn("stale", output)


class GenerateTest(ArtTestCase):
    """The optional local generator."""

    def test_a_missing_codex_cli_exits_with_a_clear_message(self) -> None:
        self.make_plan()
        buffer = io.StringIO()
        with mock.patch.object(art.shutil, "which", return_value=None), contextlib.redirect_stdout(buffer):
            code = art.main(["--config", str(self.config_path), "generate", "--plan", str(self.plan_path)])
        self.assertEqual(2, code)
        self.assertIn("codex", buffer.getvalue())

    def test_an_unknown_provider_is_refused(self) -> None:
        self.make_plan()
        with self.assertRaises(SystemExit):
            art.main(["--config", str(self.config_path), "generate", "--provider", "paid-api", "--plan", str(self.plan_path)])


if __name__ == "__main__":
    unittest.main()
