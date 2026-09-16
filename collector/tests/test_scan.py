"""Tests for the read-only inventory scan."""

from __future__ import annotations

import json
import plistlib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from collector import scan
from collector.tests.support import HomeTestCase, write, write_json


def items_by_category(inventory: dict[str, Any], category: str) -> list[dict[str, Any]]:
    """Every item of one category, in inventory order."""
    return [item for item in inventory["items"] if item["category"] == category]


def item_by_id(inventory: dict[str, Any], identifier: str) -> dict[str, Any]:
    """One item looked up by id."""
    for item in inventory["items"]:
        if item["id"] == identifier:
            return item
    raise AssertionError("no item with id " + identifier)


class RuntimeModuleTest(HomeTestCase):
    """Models, command line tools and tool connections."""

    def setUp(self) -> None:
        super().setUp()
        write_json(self.home / ".codex/models_cache.json", {"models": [
            {"slug": "gpt-5-codex", "display_name": "GPT-5 Codex", "visibility": "list", "context_window": 400_000, "default_reasoning_level": "medium"},
            {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hidden"},
        ]})
        write(self.home / ".codex/config.toml", '[mcp_servers.playwright]\ncommand = "npx"\nargs = ["-y", "playwright"]\n\n[mcp_servers.github]\ncommand = "gh-mcp"\n')
        write_json(self.home / ".claude/settings.json", {
            "model": "claude-opus-5",
            "fallbackModel": ["claude-sonnet-5"],
            "modelSettings": {"claude-haiku-4-5": {}},
            "enabledPlugins": {"guild-kit@market": True},
        })
        write_json(self.home / ".claude.json", {
            "mcpServers": {"notion": {"command": "notion-mcp", "env": {"NOTION_TOKEN": "secret-value"}}},
            "projects": {"/somewhere/project": {"mcpServers": {"postgres": {"url": "postgres://user:pw@host/db"}}}},
        })
        write_json(self.home / ".claude/mcp-needs-auth-cache.json", {"slack": {"at": 1}})
        write_json(self.home / ".gemini/settings.json", {"model": "gemini-2.5-pro"})
        write(self.home / ".config/goose/config.yaml", "GOOSE_MODEL: goose-model-1\nmodel: goose-model-1\n")
        write(self.home / ".grok/config.toml", 'model = "grok-4"\napi_key = "sk-do-not-collect-me-1234567890"\n')
        write_json(self.home / "Projects/app/.mcp.json", {"mcpServers": {"figma": {"command": "figma-mcp"}}})
        write_json(self.home / "Projects/app/node_modules/deep/.mcp.json", {"mcpServers": {"ignored": {}}})

    def scan_runtime(self, **overrides: Any) -> dict[str, Any]:
        """Run the runtime module on the fixture home."""
        return scan.run_scan(self.config(**overrides), "main", ["runtime"], remote=False)

    def test_models_come_from_every_configuration_file(self) -> None:
        inventory = self.scan_runtime()
        names = {item["name"] for item in items_by_category(inventory, "model")}
        self.assertIn("GPT-5 Codex", names)
        self.assertIn("claude-opus-5", names)
        self.assertIn("claude-sonnet-5", names)
        self.assertIn("claude-haiku-4-5", names)
        self.assertIn("gemini-2.5-pro", names)
        self.assertIn("goose-model-1", names)
        self.assertIn("grok-4", names)
        self.assertNotIn("Hidden", names)

    def test_connections_publish_names_but_never_values(self) -> None:
        inventory = self.scan_runtime()
        names = {item["name"] for item in items_by_category(inventory, "connection")}
        self.assertEqual({"playwright", "github", "notion", "postgres", "slack", "figma", "guild-kit@market"}, names)
        self.assertNotIn("ignored", names)
        rendered = json.dumps(inventory)
        for value in ("npx", "gh-mcp", "notion-mcp", "secret-value", "postgres://", "sk-do-not-collect-me"):
            self.assertNotIn(value, rendered)

    def test_pending_authentication_needs_attention(self) -> None:
        inventory = self.scan_runtime()
        self.assertEqual("attention", item_by_id(inventory, "connection-claude-auth-slack")["status"])

    def test_cli_records_its_version_line(self) -> None:
        with mock.patch.object(scan.shutil, "which", return_value="/opt/tools/bin/demo"), \
             mock.patch.object(scan, "run_command", return_value=(True, "demo 1.2.3\nextra line\n")):
            inventory = self.scan_runtime(collector={"clis": ["demo"]})
        item = item_by_id(inventory, "cli-demo")
        self.assertEqual("observed", item["status"])
        self.assertEqual("demo 1.2.3", item["evidence"][0]["detail"])
        self.assertEqual("/opt/tools/bin/demo", item["meta"]["executable"])

    def test_only_version_is_ever_executed(self) -> None:
        calls: list[list[str]] = []

        def record(args, timeout=10, stdin_text=None):
            calls.append(list(args))
            return True, "demo 1.0"

        with mock.patch.object(scan.shutil, "which", return_value="/opt/tools/bin/demo"), \
             mock.patch.object(scan, "run_command", side_effect=record):
            self.scan_runtime(collector={"clis": ["demo"]})
        self.assertEqual([["demo", "--version"]], calls)


class SkillModuleTest(HomeTestCase):
    """Skill discovery, deduplication and summaries."""

    def setUp(self) -> None:
        super().setUp()
        write(self.home / ".agents/skills/release-notes/SKILL.md", "---\nname: release-notes\ndescription: >\n  Turn merged pull requests into readable notes.\n  Use when a version ships.\n---\nbody\n")
        write(self.home / ".claude/skills/perf-budget/SKILL.md", '---\nname: perf-budget\ndescription: "Set and check a performance budget."\n---\n')
        write(self.home / ".codex/plugins/cache/vendor/kit/1.0/skills/data-cleanup/SKILL.md", "---\nname: data-cleanup\ndescription: Normalise a messy export.\n---\n")

    def test_frontmatter_description_becomes_the_summary(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["skills"], remote=False)
        item = item_by_id(inventory, "skill-release-notes")
        self.assertEqual("Turn merged pull requests into readable notes. Use when a version ships.", item["summary"])
        self.assertEqual([{"kind": "shared", "label": "Shared skills"}], item["meta"]["sources"])
        self.assertEqual("shared", item["tags"][0])

    def test_plugin_and_local_skills_are_tagged_by_source(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["skills"], remote=False)
        self.assertEqual(["claude"], item_by_id(inventory, "skill-perf-budget")["tags"])
        self.assertEqual(["plugin"], item_by_id(inventory, "skill-data-cleanup")["tags"])

    def test_a_symlinked_skill_is_counted_once(self) -> None:
        link = self.home / ".codex/skills/release-notes"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(self.home / ".agents/skills/release-notes", target_is_directory=True)
        inventory = scan.run_scan(self.config(), "main", ["skills"], remote=False)
        matching = [item for item in items_by_category(inventory, "skill") if item["id"] == "skill-release-notes"]
        self.assertEqual(1, len(matching))
        self.assertEqual(1, matching[0]["meta"]["sourceCount"])

    def test_long_descriptions_are_trimmed(self) -> None:
        write(self.home / ".agents/skills/long-one/SKILL.md", "---\nname: long-one\ndescription: " + ("word " * 200) + "\n---\n")
        inventory = scan.run_scan(self.config(), "main", ["skills"], remote=False)
        summary = item_by_id(inventory, "skill-long-one")["summary"]
        self.assertLessEqual(len(summary), 300)
        self.assertGreater(len(summary), 280)


class AgentModuleTest(HomeTestCase):
    """Agent rosters from Claude definitions, plugins and Codex profiles."""

    def setUp(self) -> None:
        super().setUp()
        write(self.home / ".claude/agents/compass.md", "---\nname: compass\ndescription: Architecture reviews, dependency mapping, migration plans\nmodel: opus\n---\nbody\n")
        write(self.home / ".claude/agents/quill.md", "---\nname: quill\ndescription: Editor. Turns rough notes into clear copy.\n---\nbody\n")
        write(self.home / ".claude/plugins/cache/vendor/kit/agents/scout.md", "---\nname: scout\ndescription: Research and cited summaries\n---\n")
        write(self.home / ".codex/agents/worker.toml", 'model = "gpt-5-codex"\nmodel_reasoning_effort = "high"\n')

    def test_strengths_are_derived_from_the_description(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["agents"], remote=False)
        compass = item_by_id(inventory, "agent-compass")
        self.assertEqual("compass", compass["name"])
        self.assertEqual(["Architecture reviews", "dependency mapping", "migration plans"], compass["meta"]["strengths"])
        self.assertEqual("Architecture reviews, dependency mapping, migration plans", compass["meta"]["exampleTask"])
        self.assertEqual("Definition names model opus", compass["meta"]["modelPolicy"])
        self.assertEqual(["claude"], compass["runtimes"])

    def test_configured_roles_win_over_derived_ones(self) -> None:
        roles = {"compass": {"title": "Systems architect", "strengths": ["Sequencing work"], "exampleTask": "Plan the split."}}
        inventory = scan.run_scan(self.config(roles=roles), "main", ["agents"], remote=False)
        compass = item_by_id(inventory, "agent-compass")
        self.assertEqual("Systems architect", compass["meta"]["title"])
        self.assertEqual(["Sequencing work"], compass["meta"]["strengths"])
        self.assertEqual("Plan the split.", compass["meta"]["exampleTask"])

    def test_sentence_descriptions_still_yield_strengths(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["agents"], remote=False)
        quill = item_by_id(inventory, "agent-quill")
        self.assertTrue(quill["meta"]["strengths"])
        self.assertTrue(quill["meta"]["exampleTask"])

    def test_codex_profiles_publish_model_keys_only(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["agents"], remote=False)
        worker = item_by_id(inventory, "agent-worker")
        self.assertEqual(["codex"], worker["runtimes"])
        self.assertEqual("gpt-5-codex", worker["meta"]["model"])
        self.assertEqual("high", worker["meta"]["effort"])

    def test_plugin_agents_are_marked_by_origin(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["agents"], remote=False)
        self.assertEqual("plugin", item_by_id(inventory, "agent-scout")["meta"]["origin"])


class HarnessModuleTest(HomeTestCase):
    """Hooks, rule documents and instruction files."""

    def setUp(self) -> None:
        super().setUp()
        write_json(self.home / ".claude/settings.json", {"hooks": {"SessionStart": [{"one": 1}, {"two": 2}], "Stop": [{"three": 3}]}})
        write(self.home / ".claude/rules/review.md", "# Review rules\n\nEvery change is reviewed before it ships.\n")
        write(self.home / ".claude/CLAUDE.md", "instructions")
        write(self.home / ".codex/AGENTS.md", "instructions")

    def test_hooks_publish_event_and_group_count_only(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["harness"], remote=False)
        hook = item_by_id(inventory, "harness-hook-sessionstart")
        self.assertEqual({"event": "SessionStart", "hookGroups": 2}, hook["meta"])
        self.assertNotIn("one", json.dumps(inventory))

    def test_rule_summary_is_the_first_body_line(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["harness"], remote=False)
        self.assertEqual("Every change is reviewed before it ships.", item_by_id(inventory, "harness-rule-review")["summary"])

    def test_instruction_files_are_recorded_by_presence(self) -> None:
        inventory = scan.run_scan(self.config(), "main", ["harness"], remote=False)
        identifiers = {item["id"] for item in inventory["items"]}
        self.assertIn("harness-claude-instructions", identifiers)
        self.assertIn("harness-codex-instructions", identifiers)


class AutomationModuleTest(HomeTestCase):
    """Scheduled jobs, their state and their privacy rules."""

    def setUp(self) -> None:
        super().setUp()
        self.write_job("local.daily-digest", {"StartCalendarInterval": {"Hour": 7}})
        self.write_job("local.client.billing", {"StartInterval": 3600})
        self.write_job("com.apple.something", {"RunAtLoad": True})
        self.rows = {"local.daily-digest": (None, 0), "local.client.billing": (4321, 0)}

    def write_job(self, label: str, extra: dict[str, Any]) -> Path:
        """Write one launchd job file into the fixture home."""
        path = self.home / "Library/LaunchAgents" / (label + ".plist")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Label": label, "ProgramArguments": ["/usr/bin/secret-tool", "--token", "xoxb-not-collected"]}
        payload.update(extra)
        path.write_bytes(plistlib.dumps(payload))
        return path

    def run_automation(self, **overrides: Any) -> dict[str, Any]:
        """Run the automation module with stubbed scheduler output."""
        with mock.patch.object(scan, "launchctl_rows", return_value=self.rows), \
             mock.patch.object(scan, "run_command", return_value=(False, "")):
            return scan.run_scan(self.config(**overrides), "main", ["automation"], remote=False)

    def test_excluded_labels_are_skipped(self) -> None:
        inventory = self.run_automation()
        names = {item["name"] for item in inventory["items"]}
        self.assertEqual({"local.daily-digest", "local.client.billing"}, names)

    def test_scheduler_state_becomes_status(self) -> None:
        inventory = self.run_automation()
        waiting = item_by_id(inventory, "automation-local-daily-digest")
        self.assertEqual("observed", waiting["status"])
        self.assertTrue(waiting["schedule"]["loaded"])
        self.assertFalse(waiting["schedule"]["running"])
        self.assertEqual("calendar", waiting["schedule"]["kind"])
        running = item_by_id(inventory, "automation-local-client-billing")
        self.assertTrue(running["schedule"]["running"])

    def test_failed_jobs_ask_for_attention(self) -> None:
        self.rows["local.daily-digest"] = (None, 2)
        inventory = self.run_automation()
        item = item_by_id(inventory, "automation-local-daily-digest")
        self.assertEqual("attention", item["status"])
        self.assertEqual(2, item["schedule"]["lastExitCode"])

    def test_private_labels_are_published_opaquely(self) -> None:
        inventory = self.run_automation(collector={"jobs": {"privatePatterns": ["*.client.*"]}})
        private = [item for item in inventory["items"] if item["id"].startswith("automation-private-")]
        self.assertEqual(1, len(private))
        entry = private[0]
        self.assertEqual("Private job", entry["name"])
        self.assertNotIn("label", entry["meta"])
        self.assertNotIn("sourceFile", entry["meta"])
        rendered = json.dumps(inventory)
        self.assertNotIn("local.client.billing", rendered)
        self.assertIn("opaque id", " ".join(inventory["notes"]))

    def test_job_arguments_are_never_collected(self) -> None:
        rendered = json.dumps(self.run_automation())
        self.assertNotIn("secret-tool", rendered)
        self.assertNotIn("xoxb-not-collected", rendered)

    def test_crontab_lines_keep_only_the_expression(self) -> None:
        crontab = "SHELL=/bin/sh\n# a comment\n*/5 * * * * /usr/local/bin/backup --to /mnt/disk # backup-check\n0 6 * * 1 /usr/bin/report\n"
        entries = scan.parse_crontab(crontab)
        self.assertEqual([("*/5 * * * *", "backup-check"), ("0 6 * * 1", "")], entries)

    def test_crontab_items_hide_the_command(self) -> None:
        crontab = "*/5 * * * * /usr/local/bin/backup --token abc # backup-check\n"
        with mock.patch.object(scan, "launchctl_rows", return_value={}), \
             mock.patch.object(scan, "run_command", return_value=(True, crontab)):
            inventory = scan.run_scan(self.config(), "main", ["automation"], remote=False)
        item = item_by_id(inventory, "automation-cron-backup-check")
        self.assertEqual({"expression": "*/5 * * * *"}, item["schedule"]["calendar"])
        self.assertNotIn("/usr/local/bin/backup", json.dumps(inventory))


class MergeTest(HomeTestCase):
    """Inventory assembly, remote merging and redaction."""

    def make_item(self, identifier: str, name: str, category: str = "cli", machine: str = "main") -> dict[str, Any]:
        """A minimal valid item for merge tests."""
        return {
            "id": identifier, "name": name, "category": category, "summary": "summary",
            "tags": [], "status": "observed", "statusLabel": "found", "machines": [machine],
            "runtimes": [], "evidence": [{"source": "s", "detail": "d", "checkedAt": scan.now()}],
        }

    def test_same_item_on_two_machines_is_merged(self) -> None:
        items = [self.make_item("cli-claude", "claude")]
        added = scan.merge_remote_items(items, [self.make_item("cli-claude", "claude", machine="laptop")], "laptop")
        self.assertEqual(0, added)
        self.assertEqual(1, len(items))
        self.assertEqual(["main", "laptop"], items[0]["machines"])
        self.assertEqual(2, len(items[0]["evidence"]))

    def test_unlike_items_that_share_an_id_are_kept_apart(self) -> None:
        items = [self.make_item("cli-tool", "tool")]
        added = scan.merge_remote_items(items, [self.make_item("cli-tool", "other tool", machine="laptop")], "laptop")
        self.assertEqual(1, added)
        self.assertEqual(["cli-tool", "cli-tool-laptop"], [item["id"] for item in items])

    def test_remote_only_items_are_added(self) -> None:
        items = [self.make_item("cli-claude", "claude")]
        scan.merge_remote_items(items, [self.make_item("cli-codex", "codex", machine="laptop")], "laptop")
        self.assertEqual(["laptop"], items[1]["machines"])

    def test_duplicate_ids_fail_the_build(self) -> None:
        context = scan.Context(self.config(), "main")
        parts = {"runtime": {"module": "runtime", "collectedAt": scan.now(), "items": [self.make_item("cli-a", "a"), self.make_item("cli-a", "a")]}}
        with self.assertRaises(ValueError):
            scan.build_inventory(context, parts)

    def test_sources_count_every_item(self) -> None:
        context = scan.Context(self.config(), "main")
        parts = {
            "runtime": {"module": "runtime", "collectedAt": scan.now(), "items": [self.make_item("cli-a", "a")]},
            "skills": {"module": "skills", "collectedAt": scan.now(), "items": [self.make_item("skill-b", "b", "skill")]},
        }
        inventory = scan.build_inventory(context, parts)
        self.assertEqual(len(inventory["items"]), sum(source["count"] for source in inventory["sources"]))
        self.assertEqual(["runtime", "skills"], [source["name"] for source in inventory["sources"]])

    def test_dangling_related_ids_are_dropped(self) -> None:
        context = scan.Context(self.config(), "main")
        item = self.make_item("cli-a", "a")
        item["relatedIds"] = ["cli-a", "cli-missing"]
        parts = {"runtime": {"module": "runtime", "collectedAt": scan.now(), "items": [item]}}
        inventory = scan.build_inventory(context, parts)
        self.assertEqual(["cli-a"], inventory["items"][0]["relatedIds"])

    def test_home_paths_are_redacted_from_free_text(self) -> None:
        context = scan.Context(self.config(), "main")
        item = self.make_item("cli-a", "a")
        item["summary"] = "Reads " + str(self.home) + "/notes and /Users/someone/else/file"
        parts = {"runtime": {"module": "runtime", "collectedAt": scan.now(), "items": [item]}}
        inventory = scan.build_inventory(context, parts)
        rendered = json.dumps(inventory)
        self.assertNotIn("/Users/", rendered)
        self.assertIn("~/notes", inventory["items"][0]["summary"])

    def test_unreachable_machines_keep_their_last_record(self) -> None:
        previous = {"id": "laptop", "status": "observed", "checkedAt": "2026-01-01T00:00:00Z", "skillCount": 12}
        record = scan.unavailable_machine({"id": "laptop", "host": "laptop.local"}, previous, scan.now())
        self.assertEqual("unavailable", record["status"])
        self.assertTrue(record["stale"])
        self.assertEqual(12, record["skillCount"])
        self.assertEqual("2026-01-01T00:00:00Z", record["lastSuccessfulAt"])

    def test_a_remote_scan_is_merged_into_the_local_one(self) -> None:
        write(self.home / ".claude/CLAUDE.md", "instructions")
        remote_payload = {
            "items": [self.make_item("harness-claude-instructions", "Claude instructions", "harness", "laptop"), self.make_item("cli-only-there", "only-there", "cli", "laptop")],
            "machines": {"laptop": {"id": "laptop", "status": "observed", "checkedAt": scan.now(), "os": "Ubuntu 24.04"}},
        }
        config = self.config(machines=[
            {"id": "main", "label": "This machine", "kind": "local"},
            {"id": "laptop", "label": "Laptop", "kind": "ssh", "host": "laptop.local"},
        ])
        with mock.patch.object(scan, "remote_scan", return_value=remote_payload):
            inventory = scan.run_scan(config, "main", ["harness"], remote=True)
        self.assertEqual({"main", "laptop"}, set(inventory["machines"]))
        merged = item_by_id(inventory, "harness-claude-instructions")
        self.assertEqual(["main", "laptop"], merged["machines"])
        self.assertEqual(["laptop"], item_by_id(inventory, "cli-only-there")["machines"])
        self.assertEqual(len(inventory["items"]), sum(source["count"] for source in inventory["sources"]))
        self.assertIn("laptop", [source["name"] for source in inventory["sources"]])

    def test_an_unreachable_remote_is_reported_in_the_notes(self) -> None:
        config = self.config(machines=[
            {"id": "main", "label": "This machine", "kind": "local"},
            {"id": "laptop", "label": "Laptop", "kind": "ssh", "host": "laptop.local"},
        ])
        with mock.patch.object(scan, "remote_scan", return_value=None):
            inventory = scan.run_scan(config, "main", ["harness"], remote=True)
        self.assertEqual("unavailable", inventory["machines"]["laptop"]["status"])
        self.assertIn("could not be reached", " ".join(inventory["notes"]))


class FrontmatterTest(HomeTestCase):
    """The YAML-ish frontmatter reader."""

    def parse(self, text: str) -> dict[str, str]:
        """Parse frontmatter from a temporary file."""
        return scan.frontmatter(write(self.home / "doc.md", text))

    def test_inline_values(self) -> None:
        self.assertEqual({"name": "one", "description": "two"}, self.parse("---\nname: one\ndescription: two\n---\nbody"))

    def test_folded_values(self) -> None:
        parsed = self.parse("---\nname: one\ndescription: >\n  first line\n  second line\n---\n")
        self.assertEqual("first line second line", parsed["description"])

    def test_continued_plain_values(self) -> None:
        parsed = self.parse("---\nname: one\ndescription: first line\n  second line\nmodel: opus\n---\n")
        self.assertEqual("first line second line", parsed["description"])
        self.assertEqual("opus", parsed["model"])

    def test_quotes_are_stripped(self) -> None:
        self.assertEqual("quoted", self.parse('---\nname: "quoted"\n---\n')["name"])

    def test_missing_frontmatter_is_empty(self) -> None:
        self.assertEqual({}, self.parse("# just a heading\n"))


class ModuleSelectionTest(HomeTestCase):
    """Command line module selection."""

    def test_modules_are_validated_and_ordered(self) -> None:
        self.assertEqual(["runtime", "skills"], scan.parse_modules("skills,runtime"))

    def test_unknown_modules_are_refused(self) -> None:
        with self.assertRaises(Exception):
            scan.parse_modules("runtime,nonsense")

    def test_a_partial_scan_says_so(self) -> None:
        write(self.home / ".claude/CLAUDE.md", "instructions")
        inventory = scan.run_scan(self.config(), "main", ["harness"], remote=False)
        self.assertIn("subset of the collector modules", " ".join(inventory["notes"]))


if __name__ == "__main__":
    unittest.main()
