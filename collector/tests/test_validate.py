"""Tests for the pre-publication validator."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collector import demo, validate
from collector.tests.support import HomeTestCase


def problems(inventory: dict[str, Any]) -> list[str]:
    """Validate an inventory and return the reported problems."""
    report = validate.Report()
    validate.validate_inventory(inventory, report)
    return report.problems


def stats_problems(payload: dict[str, Any]) -> list[str]:
    """Validate a statistics snapshot and return the reported problems."""
    report = validate.Report()
    validate.validate_stats(payload, report)
    return report.problems


class InventoryValidationTest(HomeTestCase):
    """Rules that keep an inventory publishable."""

    def setUp(self) -> None:
        super().setUp()
        self.inventory = demo.build_inventory(datetime.now(timezone.utc).replace(microsecond=0))

    def mutate(self, change) -> list[str]:
        """Apply a change to a copy of the demo inventory and validate it."""
        candidate = copy.deepcopy(self.inventory)
        change(candidate)
        return problems(candidate)

    def test_the_demo_inventory_is_valid(self) -> None:
        self.assertEqual([], problems(self.inventory))

    def test_duplicate_ids_are_rejected(self) -> None:
        def change(inventory: dict[str, Any]) -> None:
            inventory["items"].append(copy.deepcopy(inventory["items"][0]))
            inventory["sources"][0]["count"] += 1
        self.assertTrue(any("duplicate item id" in problem for problem in self.mutate(change)))

    def test_ids_outside_the_allowed_alphabet_are_rejected(self) -> None:
        reported = self.mutate(lambda inventory: inventory["items"][0].update({"id": "cli claude!"}))
        self.assertTrue(any("id must match" in problem for problem in reported))

    def test_unknown_categories_are_rejected(self) -> None:
        self.assertTrue(any("unknown category" in problem for problem in self.mutate(lambda inventory: inventory["items"][0].update({"category": "weapon"}))))

    def test_unknown_statuses_are_rejected(self) -> None:
        self.assertTrue(any("unknown status" in problem for problem in self.mutate(lambda inventory: inventory["items"][0].update({"status": "great"}))))

    def test_every_item_needs_evidence(self) -> None:
        self.assertTrue(any("evidence" in problem for problem in self.mutate(lambda inventory: inventory["items"][0].update({"evidence": []}))))

    def test_meta_keys_outside_the_allow_list_are_rejected(self) -> None:
        reported = self.mutate(lambda inventory: inventory["items"][0].setdefault("meta", {}).update({"apiKey": "value"}))
        self.assertTrue(any("meta has keys outside" in problem for problem in reported))

    def test_items_must_name_a_declared_machine(self) -> None:
        reported = self.mutate(lambda inventory: inventory["items"][0].update({"machines": ["desktop"]}))
        self.assertTrue(any("not declared in machines" in problem for problem in reported))

    def test_machine_ids_follow_the_configured_pattern(self) -> None:
        def change(inventory: dict[str, Any]) -> None:
            inventory["machines"]["Main Machine"] = inventory["machines"].pop("main")
        self.assertTrue(any("id must match" in problem for problem in self.mutate(change)))

    def test_source_counts_must_add_up(self) -> None:
        reported = self.mutate(lambda inventory: inventory["sources"][0].update({"count": 999}))
        self.assertTrue(any("add up to the item count" in problem for problem in reported))

    def test_agents_need_strengths_and_an_example(self) -> None:
        def change(inventory: dict[str, Any]) -> None:
            for item in inventory["items"]:
                if item["category"] == "agent":
                    item["meta"].pop("strengths")
                    item["meta"].pop("exampleTask")
                    break
        reported = self.mutate(change)
        self.assertTrue(any("meta.strengths" in problem for problem in reported))
        self.assertTrue(any("meta.exampleTask" in problem for problem in reported))

    def test_private_jobs_must_not_carry_their_label(self) -> None:
        def change(inventory: dict[str, Any]) -> None:
            for item in inventory["items"]:
                if item["id"].startswith("automation-private-"):
                    item["meta"]["label"] = "local.client.billing"
                    break
        self.assertTrue(any("private jobs must not publish" in problem for problem in self.mutate(change)))

    def test_related_ids_must_exist(self) -> None:
        reported = self.mutate(lambda inventory: inventory["items"][0].update({"relatedIds": ["skill-nothing"]}))
        self.assertTrue(any("missing item" in problem for problem in reported))

    def test_only_automation_items_carry_a_schedule(self) -> None:
        def change(inventory: dict[str, Any]) -> None:
            inventory["items"][0]["schedule"] = {"kind": "interval", "label": "Runs every 60 seconds", "intervalSeconds": 60}
        self.assertTrue(any("only automation items" in problem for problem in self.mutate(change)))

    def test_future_timestamps_are_rejected(self) -> None:
        self.assertTrue(any("collectedAt" in problem for problem in self.mutate(lambda inventory: inventory.update({"collectedAt": "2099-01-01T00:00:00Z"}))))

    def test_missing_categories_are_a_note_not_a_failure(self) -> None:
        candidate = copy.deepcopy(self.inventory)
        candidate["items"] = [item for item in candidate["items"] if item["category"] != "automation"]
        candidate["sources"] = [source for source in candidate["sources"] if source["name"] != "automation"]
        report = validate.Report()
        validate.validate_inventory(candidate, report)
        self.assertEqual([], report.problems)
        self.assertTrue(any("automation" in note for note in report.notes))


class SecretScanTest(HomeTestCase):
    """Nothing secret-looking may reach the server."""

    def write_inventory(self, change=None) -> Path:
        """Write the demo inventory to a file, optionally altered."""
        inventory = demo.build_inventory(datetime.now(timezone.utc).replace(microsecond=0))
        if change:
            change(inventory)
        path = self.home / "inventory.json"
        path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_a_clean_document_passes(self) -> None:
        ok, message = validate.validate_file(self.write_inventory(), as_stats=False)
        self.assertTrue(ok, message)
        self.assertTrue(message.startswith("PASS"))

    def test_absolute_home_paths_are_rejected(self) -> None:
        path = self.write_inventory(lambda inventory: inventory["items"][0].update({"summary": "Reads /Users/someone/notes"}))
        ok, message = validate.validate_file(path, as_stats=False)
        self.assertFalse(ok)
        self.assertIn("absolute home path", message)

    def test_api_keys_are_rejected(self) -> None:
        path = self.write_inventory(lambda inventory: inventory["notes"].append("key sk-proj-abcdefghijklmnopqrstuvwxyz0123"))
        ok, message = validate.validate_file(path, as_stats=False)
        self.assertFalse(ok)
        self.assertIn("OpenAI-style key", message)

    def test_bearer_tokens_are_rejected(self) -> None:
        path = self.write_inventory(lambda inventory: inventory["notes"].append("Authorization Bearer abcdefghijklmnopqrstuvwxyz"))
        self.assertFalse(validate.validate_file(path, as_stats=False)[0])

    def test_private_keys_are_rejected(self) -> None:
        path = self.write_inventory(lambda inventory: inventory["notes"].append("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertFalse(validate.validate_file(path, as_stats=False)[0])


class EnvelopeTest(HomeTestCase):
    """The upload envelope around an inventory."""

    def envelope(self, **overrides: Any) -> dict[str, Any]:
        """A valid envelope, with the given fields replaced."""
        moment = datetime.now(timezone.utc).replace(microsecond=0)
        inventory = demo.build_inventory(moment)
        payload = {
            "schemaVersion": 1, "source": "collector", "revision": 3,
            "collectedAt": inventory["collectedAt"], "heartbeatAt": inventory["collectedAt"], "inventory": inventory,
        }
        payload.update(overrides)
        return payload

    def test_a_valid_envelope_passes(self) -> None:
        report = validate.Report()
        validate.validate_envelope(self.envelope(), report)
        self.assertEqual([], report.problems)

    def test_the_source_must_be_the_collector(self) -> None:
        report = validate.Report()
        validate.validate_envelope(self.envelope(source="laptop"), report)
        self.assertTrue(any("literal 'collector'" in problem for problem in report.problems))

    def test_revisions_start_at_one(self) -> None:
        report = validate.Report()
        validate.validate_envelope(self.envelope(revision=0), report)
        self.assertTrue(any("at least 1" in problem for problem in report.problems))

    def test_a_file_with_an_envelope_is_validated_whole(self) -> None:
        path = self.home / "envelope.json"
        path.write_text(json.dumps(self.envelope(source="laptop")), encoding="utf-8")
        ok, message = validate.validate_file(path, as_stats=False)
        self.assertFalse(ok)
        self.assertIn("collector", message)


class StatsValidationTest(HomeTestCase):
    """Rules that keep a statistics snapshot publishable."""

    def setUp(self) -> None:
        super().setUp()
        self.stats = demo.build_stats(datetime.now(timezone.utc).replace(microsecond=0))

    def mutate(self, change) -> list[str]:
        """Apply a change to a copy of the demo snapshot and validate it."""
        candidate = copy.deepcopy(self.stats)
        change(candidate)
        return stats_problems(candidate)

    def test_the_demo_snapshot_is_valid(self) -> None:
        self.assertEqual([], stats_problems(self.stats))

    def test_seven_days_are_required(self) -> None:
        self.assertTrue(any("exactly 7" in problem for problem in self.mutate(lambda stats: stats["days"].pop())))

    def test_days_must_be_consecutive(self) -> None:
        self.assertTrue(any("consecutive" in problem for problem in self.mutate(lambda stats: stats["days"][3].update({"date": "2020-01-01"}))))

    def test_today_must_equal_the_last_day(self) -> None:
        self.assertTrue(any("must equal the last day" in problem for problem in self.mutate(lambda stats: stats["today"].update({"input": stats["today"]["input"] + 1, "total": stats["today"]["total"] + 1}))))

    def test_provider_totals_must_add_up(self) -> None:
        self.assertTrue(any("add up to stats.totals" in problem for problem in self.mutate(lambda stats: stats["providers"][0]["totals"].update({"output": 1, "total": stats["providers"][0]["totals"]["input"] + 1}))))

    def test_cached_input_cannot_exceed_input(self) -> None:
        self.assertTrue(any("totals" in problem for problem in self.mutate(lambda stats: stats["totals"].update({"cachedInput": stats["totals"]["input"] + 1}))))

    def test_the_time_zone_must_be_a_real_zone(self) -> None:
        self.assertTrue(any("IANA" in problem or "known" in problem for problem in self.mutate(lambda stats: stats.update({"timezone": "Mars/Olympus"}))))

    def test_between_one_and_six_providers(self) -> None:
        self.assertTrue(any("1..6" in problem for problem in self.mutate(lambda stats: stats.update({"providers": []}))))

    def test_a_single_provider_is_allowed(self) -> None:
        def change(stats: dict[str, Any]) -> None:
            keeper = stats["providers"][0]
            stats["providers"] = [keeper]
            stats["totals"] = dict(keeper["totals"])
            stats["today"] = dict(keeper["today"])
            for index, day in enumerate(stats["days"]):
                for key in ("input", "output", "cachedInput", "total"):
                    day[key] = keeper["today"][key] if index == 6 else 0
            stats["totals"] = {key: keeper["totals"][key] for key in ("input", "output", "cachedInput", "total")}
        reported = self.mutate(change)
        self.assertTrue(all("providers" not in problem for problem in reported), reported)

    def test_coverage_notes_are_bounded(self) -> None:
        self.assertTrue(any("coverage.notes" in problem for problem in self.mutate(lambda stats: stats["coverage"].update({"notes": ["note"] * 9}))))

    def test_the_command_line_reports_both_files(self) -> None:
        inventory_path = self.home / "inventory.json"
        inventory_path.write_text(json.dumps(demo.build_inventory(datetime.now(timezone.utc).replace(microsecond=0))), encoding="utf-8")
        stats_path = self.home / "stats.json"
        stats_path.write_text(json.dumps(self.stats), encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validate.main([str(inventory_path), "--stats", str(stats_path)])
        self.assertEqual(0, code)
        self.assertEqual(2, buffer.getvalue().count("PASS"))


if __name__ == "__main__":
    unittest.main()
