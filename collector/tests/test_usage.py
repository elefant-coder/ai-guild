"""Tests for the token usage aggregation."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from collector import usage
from collector.tests.support import HomeTestCase

UTC = ZoneInfo("UTC")


def moment(minutes_ago: int = 0) -> str:
    """An ISO timestamp a few minutes in the past."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def codex_event(timestamp: str, used_input: int, used_output: int, cached: int = 0) -> str:
    """One Codex event carrying a cumulative counter."""
    return json.dumps({
        "timestamp": timestamp,
        "payload": {"info": {"total_token_usage": {"input_tokens": used_input, "output_tokens": used_output, "cached_input_tokens": cached}}},
    })


def claude_event(timestamp: str, identifier: str, used_input: int, used_output: int, cache_read: int = 0) -> str:
    """One Claude assistant message carrying its own usage."""
    return json.dumps({
        "timestamp": timestamp,
        "message": {"id": identifier, "usage": {"input_tokens": used_input, "output_tokens": used_output, "cache_read_input_tokens": cache_read}},
    })


class CodexParsingTest(HomeTestCase):
    """Cumulative counters become per-day deltas."""

    def write_session(self, name: str, lines: list[str]) -> Path:
        """Write one Codex session log into the fixture home."""
        path = self.home / ".codex/sessions/2026/09" / (name + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_deltas_are_summed_per_day(self) -> None:
        path = self.write_session("one", [
            json.dumps({"timestamp": moment(10), "type": "session_meta", "payload": {"id": "session-one"}}),
            codex_event(moment(9), 100, 10, 50),
            codex_event(moment(8), 400, 60, 250),
        ])
        parsed = usage.parse_codex_file(path, UTC)
        today = datetime.now(UTC).date().isoformat()
        self.assertEqual("session-one", parsed["sessionId"])
        self.assertEqual(400, parsed["days"][today]["input"])
        self.assertEqual(60, parsed["days"][today]["output"])
        self.assertEqual(460, parsed["days"][today]["total"])
        self.assertTrue(parsed["complete"])

    def test_counters_that_go_backwards_never_go_negative(self) -> None:
        path = self.write_session("reset", [codex_event(moment(5), 500, 50), codex_event(moment(4), 100, 10)])
        parsed = usage.parse_codex_file(path, UTC)
        today = datetime.now(UTC).date().isoformat()
        self.assertEqual(500, parsed["days"][today]["input"])

    def test_a_capped_read_resumes_from_its_offset(self) -> None:
        lines = [codex_event(moment(9), 100, 10), codex_event(moment(8), 300, 30), codex_event(moment(7), 600, 60)]
        path = self.write_session("big", lines)
        first = usage.parse_codex_file(path, UTC, 0, len(lines[0].encode()) + 1)
        self.assertFalse(first["complete"])
        second = usage.parse_codex_file(path, UTC, first["offset"], None, first["lastCounter"], first["sessionId"])
        today = datetime.now(UTC).date().isoformat()
        self.assertEqual(600, first["days"][today]["input"] + second["days"][today]["input"])

    def test_a_resumed_session_does_not_count_the_inherited_snapshot(self) -> None:
        shared = moment(6)
        self.write_session("parent", [
            json.dumps({"timestamp": moment(9), "type": "session_meta", "payload": {"id": "parent"}}),
            codex_event(moment(8), 100, 10),
            codex_event(shared, 500, 50),
        ])
        self.write_session("child", [
            json.dumps({"timestamp": moment(6), "type": "session_meta", "payload": {"id": "child"}}),
            codex_event(shared, 500, 50),
            codex_event(moment(5), 700, 70),
        ])
        snapshot = usage.run(self.home, self.home / "state.json", ["codex"], UTC, "Test machine")
        self.assertEqual(770, snapshot["totals"]["total"])

    def test_two_independent_sessions_are_both_counted(self) -> None:
        self.write_session("a", [codex_event(moment(9), 100, 10)])
        self.write_session("b", [codex_event(moment(8), 200, 20)])
        snapshot = usage.run(self.home, self.home / "state.json", ["codex"], UTC, "Test machine")
        self.assertEqual(330, snapshot["totals"]["total"])
        self.assertEqual(2, snapshot["providers"][0]["sessions"])


class ClaudeParsingTest(HomeTestCase):
    """Message level deduplication across copied session files."""

    def write_session(self, project: str, name: str, lines: list[str]) -> Path:
        """Write one Claude session log into the fixture home."""
        path = self.home / ".claude/projects" / project / (name + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_cache_reads_count_as_input(self) -> None:
        path = self.write_session("alpha", "one", [claude_event(moment(5), "msg-1", 100, 20, 900)])
        parsed = usage.parse_claude_file(path, UTC)
        today = datetime.now(UTC).date().isoformat()
        self.assertEqual({"input": 1000, "output": 20, "cachedInput": 900, "total": 1020}, parsed["days"][today])

    def test_the_same_message_in_two_files_is_counted_once(self) -> None:
        self.write_session("alpha", "one", [claude_event(moment(5), "msg-1", 100, 20)])
        self.write_session("beta", "copy", [claude_event(moment(5), "msg-1", 100, 20)])
        snapshot = usage.run(self.home, self.home / "state.json", ["claude"], UTC, "Test machine")
        self.assertEqual(120, snapshot["totals"]["total"])

    def test_the_larger_copy_of_a_message_wins(self) -> None:
        self.write_session("alpha", "one", [claude_event(moment(5), "msg-1", 100, 20)])
        self.write_session("beta", "two", [claude_event(moment(5), "msg-1", 100, 500)])
        snapshot = usage.run(self.home, self.home / "state.json", ["claude"], UTC, "Test machine")
        self.assertEqual(600, snapshot["totals"]["total"])

    def test_message_ids_are_never_stored_in_the_cache(self) -> None:
        self.write_session("alpha", "one", [claude_event(moment(5), "msg-secret-id", 100, 20)])
        state_path = self.home / "state.json"
        usage.run(self.home, state_path, ["claude"], UTC, "Test machine")
        self.assertNotIn("msg-secret-id", state_path.read_text(encoding="utf-8"))

    def test_the_cache_file_is_private(self) -> None:
        self.write_session("alpha", "one", [claude_event(moment(5), "msg-1", 100, 20)])
        state_path = self.home / "state.json"
        usage.run(self.home, state_path, ["claude"], UTC, "Test machine")
        self.assertEqual(0o600, state_path.stat().st_mode & 0o777)


class SnapshotShapeTest(HomeTestCase):
    """The published snapshot always satisfies the schema arithmetic."""

    def build(self, providers: list[str] | None = None) -> dict[str, Any]:
        """Write one log per provider and return the snapshot."""
        codex = self.home / ".codex/sessions/one.jsonl"
        codex.parent.mkdir(parents=True, exist_ok=True)
        codex.write_text(codex_event(moment(5), 300, 30, 100) + "\n", encoding="utf-8")
        claude = self.home / ".claude/projects/alpha/one.jsonl"
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text(claude_event(moment(5), "msg-1", 200, 40, 150) + "\n", encoding="utf-8")
        return usage.run(self.home, self.home / "state.json", providers or ["codex", "claude"], UTC, "Test machine")

    def test_seven_consecutive_days_end_today(self) -> None:
        snapshot = self.build()
        dates = [day["date"] for day in snapshot["days"]]
        self.assertEqual(7, len(dates))
        self.assertEqual(datetime.now(UTC).date().isoformat(), dates[-1])
        for earlier, later in zip(dates, dates[1:]):
            self.assertEqual(timedelta(days=1), datetime.fromisoformat(later) - datetime.fromisoformat(earlier))

    def test_today_matches_the_last_day(self) -> None:
        snapshot = self.build()
        self.assertEqual({key: value for key, value in snapshot["days"][-1].items() if key != "date"}, snapshot["today"])

    def test_provider_totals_add_up(self) -> None:
        snapshot = self.build()
        for key in ("input", "output", "cachedInput", "total"):
            self.assertEqual(snapshot["totals"][key], sum(provider["totals"][key] for provider in snapshot["providers"]))
            self.assertEqual(snapshot["today"][key], sum(provider["today"][key] for provider in snapshot["providers"]))

    def test_cached_input_never_exceeds_input(self) -> None:
        snapshot = self.build()
        self.assertLessEqual(snapshot["totals"]["cachedInput"], snapshot["totals"]["input"])
        self.assertEqual(snapshot["totals"]["total"], snapshot["totals"]["input"] + snapshot["totals"]["output"])

    def test_a_provider_without_data_is_left_out(self) -> None:
        codex = self.home / ".codex/sessions/one.jsonl"
        codex.parent.mkdir(parents=True, exist_ok=True)
        codex.write_text(codex_event(moment(5), 300, 30) + "\n", encoding="utf-8")
        snapshot = usage.run(self.home, self.home / "state.json", ["codex", "claude"], UTC, "Test machine")
        self.assertEqual(["codex"], [provider["id"] for provider in snapshot["providers"]])

    def test_an_empty_machine_still_reports_one_provider(self) -> None:
        snapshot = usage.run(self.home, self.home / "state.json", ["codex", "claude"], UTC, "Test machine")
        self.assertEqual(1, len(snapshot["providers"]))
        self.assertEqual(0, snapshot["totals"]["total"])
        self.assertIsNone(snapshot["coverage"]["startedAt"])

    def test_the_scope_is_the_machine_label(self) -> None:
        self.assertEqual("Test machine", self.build()["scope"])

    def test_a_second_run_reuses_the_cache(self) -> None:
        first = self.build()
        second = usage.run(self.home, self.home / "state.json", ["codex", "claude"], UTC, "Test machine")
        self.assertEqual(first["totals"], second["totals"])

    def test_changing_the_time_zone_rebuilds_the_cache(self) -> None:
        self.build()
        snapshot = usage.run(self.home, self.home / "state.json", ["codex", "claude"], ZoneInfo("Asia/Tokyo"), "Test machine")
        self.assertEqual("Asia/Tokyo", snapshot["timezone"])
        self.assertGreater(snapshot["totals"]["total"], 0)


class ConfigurationTest(HomeTestCase):
    """Provider selection from configuration."""

    def test_unknown_providers_are_reported_not_counted(self) -> None:
        config = self.config(collector={"usage": {"providers": ["codex", "made-up"]}})
        snapshot = usage.snapshot(config, self.home, self.home / "state.json")
        self.assertEqual(["codex"], [provider["id"] for provider in snapshot["providers"]])
        self.assertTrue(any("made-up" in note for note in snapshot["coverage"]["notes"]))


if __name__ == "__main__":
    unittest.main()
