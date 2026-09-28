"""No network/model calls: native log replay, ownership, lifecycle and usage."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fluff_monitor.activity import RolloutReader, ClaudeReader, usage_counts, claude_processes
from fluff_monitor.catalog import ThreadCatalog, task_identity
from fluff_monitor.storage import atomic_json
from fluff_monitor.views import desktop_view, claude_view
from fluff_monitor.pet_state import MismatchAlerts


def record(kind, payload, at=100):
    return {"type": kind, "payload": payload,
            "timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat()}


def count(last=None, total=None):
    last = last or dict(input_tokens=100, cached_input_tokens=80, output_tokens=10)
    return {"type": "token_count", "info": {"last_token_usage": last, "total_token_usage": total or last}}


class NativeUsageTests(unittest.TestCase):
    def test_duplicate_usage_does_not_add_or_refresh_idle_age(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"a.jsonl"
            p.write_text(json.dumps(record("session_meta", {"id": "a"}, 1))+"\n")
            reader = RolloutReader(p, "a")
            for r in [record("event_msg", {"type": "task_started", "turn_id": "t"}),
                      record("event_msg", count(), 101), record("event_msg", count(), 150)]:
                with p.open("a") as f: f.write(json.dumps(r)+"\n")
            value = reader.poll()
            self.assertEqual(value["state"], "running")
            self.assertEqual(value["usage"]["observed_at"], 101)
            self.assertEqual(value["usage"]["last"]["cached_fraction"], .8)
            self.assertEqual(value["usage"]["reported_total"]["input_tokens"], 100)
            with p.open("a") as f:
                f.write(json.dumps(record("event_msg", {"type": "task_complete", "turn_id": "old"}, 151))+"\n")
            self.assertEqual(reader.poll()["state"], "running")
            with p.open("a") as f:
                f.write(json.dumps(record("event_msg", {"type": "task_complete", "turn_id": "t"}, 152))+"\n")
            self.assertEqual(reader.poll()["state"], "completed")

    def test_forked_history_is_not_current_child_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"b.jsonl"
            rows = [record("session_meta", {"id": "b", "forked_from_id": "a"}, 200),
                    record("event_msg", count(), 100), record("event_msg", {"type": "task_complete"}, 150)]
            p.write_text("\n".join(map(json.dumps, rows))+"\n")
            v = RolloutReader(p, "b").poll()
            self.assertNotIn("usage", v)
            self.assertEqual(v["state"], "unknown")

    def test_partial_line_is_not_lost_and_identity_mismatch_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"a.jsonl"
            p.write_text(json.dumps(record("session_meta", {"id": "a"}, 1))+"\n")
            reader = RolloutReader(p, "a"); reader.poll()
            line = json.dumps(record("event_msg", count(), 101))
            with p.open("a") as f: f.write(line[:30])
            self.assertNotIn("usage", reader.poll())
            with p.open("a") as f: f.write(line[30:]+"\n")
            self.assertIn("usage", reader.poll())
            self.assertFalse(RolloutReader(p, "other").poll()["available"])

    def test_invalid_and_missing_usage_are_not_zero(self):
        for v in ({}, {"input_tokens": 5, "cached_input_tokens": 6, "output_tokens": 1},
                  {"input_tokens": True, "cached_input_tokens": 0, "output_tokens": 1}):
            self.assertIsNone(usage_counts(v))
        self.assertIsNone(usage_counts(dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))["cached_fraction"])

    def test_claude_usage_has_total_input_denominator_and_deduplicates_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"c.jsonl"
            def event(at):
                return {"sessionId": "c", "timestamp": datetime.fromtimestamp(at, timezone.utc).isoformat(),
                        "cwd": "/PRIVATE/project", "type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 10, "output_tokens": 5},
                        "content": [{"type": "text", "text": "PRIVATE_RESPONSE"}]}}
            p.write_text(json.dumps(event(100))+"\n"+json.dumps(event(110))+"\n")
            value = ClaudeReader(p, "c").poll()
            self.assertEqual(value["usage"]["last"]["cached_fraction"], .8)
            self.assertEqual(value["usage"]["observed_at"], 100)
            self.assertIsNone(value["usage"]["reported_total"])
            self.assertEqual(value["state"], "completed")
            self.assertNotIn("PRIVATE", json.dumps(value))


class CatalogTests(unittest.TestCase):
    def test_hidden_child_routing_mismatch_still_notifies_under_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            alerts = MismatchAlerts(Path(directory)/"alerts.json")
            child = dict(thread_id="child", historical_mismatches=1, verdict="REROUTED",
                         response_id="r", requested="gpt-6-astra", served="gpt-6-luna", observed_at=100)
            route = dict(active=True, updated_at=100, publisher_pid=1,
                         sessions=[dict(thread_id="parent", title="설계 작업")],
                         observations=[child], activity_parent_map={"child": "parent"})
            notice = alerts.poll(route, now=100)
            self.assertEqual(notice[0]["title"], "설계 작업 · 하위 활동")
            self.assertEqual(notice[0]["served"], "gpt-6-luna")
            self.assertEqual(alerts.poll(route, now=101), [])

    def test_subagents_and_internal_activity_never_become_main_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath = Path(directory)/"db"
            source = json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "p", "agent_nickname": "Worker"}}})
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads(id TEXT,title TEXT,name TEXT,source TEXT,archived INTEGER)")
                db.executemany("INSERT INTO threads VALUES(?,?,?,?,0)", [("p", None, "Parent", "vscode"),
                    ("c", None, None, source), ("i", None, None, json.dumps({"subagent": "compact"})),
                    ("u", None, None, "vscode"), ("old", None, "Old", "vscode")])
            catalog = ThreadCatalog(dbpath)
            atomic_json(Path(directory)/"desktop.json", dict(active=True, updated_at=1000, sessions=[
                dict(thread_id=k, request_started_at=10, observed_at=11, status="completed", connected=True)
                for k in ("c", "i", "u", "p", "old")]))
            metrics = {"updated_at": 1000, "catalog_available": True, "threads": {
                "p": {"state": "completed", "state_at": 20},
                "c": {"parent_thread_id": "p", "state": "running", "state_at": 999},
                "u": {"state": "running", "state_at": 999},
                "old": {"state": "completed", "state_at": 20}}}
            v = desktop_view(directory, now=1000, catalog=catalog, activity=metrics)
            self.assertEqual([s["thread_id"] for s in v["sessions"]], ["u", "p"])
            self.assertEqual(v["history_count"], 1)
            self.assertEqual(v["sessions"][1]["children"]["running"], 1)
            fixed = desktop_view(directory, now=1000, catalog=catalog, activity=metrics, thread_id="old")
            self.assertEqual(fixed["thread_id"], "old")
            self.assertTrue(fixed["selection_expired"])
            self.assertNotIn("old", [s["thread_id"] for s in fixed["sessions"]])
            self.assertNotIn("served", fixed)
            history = desktop_view(directory, now=1000, catalog=catalog, activity=metrics, include_history=True)
            self.assertIn("old", [s["thread_id"] for s in history["sessions"]])
            metrics["threads"]["c"].update(state="completed", state_at=900)
            v = desktop_view(directory, now=1000, catalog=catalog, activity=metrics)
            self.assertNotIn("p", [s["thread_id"] for s in v["sessions"]])

    def test_parent_resolves_even_when_only_child_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath = Path(directory)/"db"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads(id TEXT,title TEXT,source TEXT)")
                db.executemany("INSERT INTO threads VALUES(?,?,?)", [("p", "Parent", "vscode"),
                    ("c", None, json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}))])
            catalog = ThreadCatalog(dbpath)
            self.assertEqual(catalog.resolve(["c"]), {"c": None})
            self.assertEqual(catalog.metadata["p"]["title"], "Parent")

    def test_claude_automatically_follows_live_cli_and_fixed_selection_never_borrows(self):
        metrics = dict(updated_at=1000, claude_sessions={
            "old": dict(session_id="old", process_alive=False, state="offline", state_at=10),
            "new": dict(session_id="new", process_alive=True, state="completed", state_at=990, served="claude-opus-5-5")})
        self.assertEqual(claude_view(metrics, now=1000)["session_id"], "new")
        self.assertEqual(len(claude_view(metrics, now=1000)["sessions"]), 1)
        self.assertNotIn("served", claude_view(metrics, "absent", now=1000))
        self.assertEqual(claude_view(metrics, now=1020)["state"], "unknown")
        expired = claude_view(metrics, "old", now=1000)
        self.assertTrue(expired["selection_expired"])
        self.assertNotIn("served", expired)

    def test_recent_closed_claude_logs_do_not_reappear_through_history_or_selection(self):
        raw={f"old{i}":dict(session_id=f"old{i}",state="offline",process_alive=False,state_at=999) for i in range(46)}
        raw['live']=dict(session_id='live',state='completed',process_alive=True,state_at=990)
        metrics=dict(updated_at=1000,claude_sessions=raw,
            claude_roles=dict(implementation=dict(title='NBV 구현 담당',session_id='live')))
        for history in (False,True):
            view=claude_view(metrics,'role:implementation',now=1000,include_history=history)
            self.assertEqual([s['selection_key'] for s in view['sessions']],['role:implementation'])
        self.assertTrue(claude_view(metrics,'old3',now=1000,include_history=True)['selection_expired'])
        self.assertEqual(len(metrics['claude_sessions']),47,'filtering must not delete native history')

    def test_claude_role_rotation_replaces_one_slot_without_borrowing_previous_usage(self):
        metrics = dict(updated_at=1000, claude_sessions={
            "old": dict(session_id="old", process_alive=True, state="running", state_at=999, served="old-model", usage={"old": True})},
            claude_roles={"implementation": {"title": "NBV 구현", "session_id": "new", "previous_session_id": "old"}})
        pending = claude_view(metrics, "role:implementation", now=1000)
        self.assertEqual(len(pending["sessions"]), 1)
        self.assertEqual(pending["state"], "handoff")
        self.assertIsNone(pending["served"])
        self.assertIsNone(pending["usage"])
        metrics["claude_sessions"]["new"] = dict(session_id="new", process_alive=True, state="completed", state_at=1000, served="new-model")
        ready = claude_view(metrics, "role:implementation", now=1000)
        self.assertEqual(len(ready["sessions"]), 1)
        self.assertEqual(ready["title"], "NBV 구현")
        self.assertEqual(ready["served"], "new-model")


if __name__ == "__main__":
    unittest.main()
