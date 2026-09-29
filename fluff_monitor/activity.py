"""Passive task/usage collector shared by Fluff and local meeting boards.

Only metadata is published. No provider calls, prompts, answers or credentials.
The existing routing capture stays untouched, so active Codex work need not restart.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
import uuid
import sys

from .catalog import ThreadCatalog
from . import __version__
from .storage import Publisher, SnapshotReader, read_json, state_dir
from .platform_support import (lock_exclusive, windows_claude_processes,
                               cli_argument, is_claude_cli, native_claude_session)

MAX_READ = 4 * 1024 * 1024
TERMINAL = {"task_complete": "completed", "turn_aborted": "interrupted"}


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def usage_counts(value):
    if not isinstance(value, dict):
        return None
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(type(value.get(k)) is not int or value[k] < 0 for k in keys):
        return None
    if value["cached_input_tokens"] > value["input_tokens"]:
        return None
    result = {k: value[k] for k in keys}
    for k in ("reasoning_output_tokens", "cache_write_input_tokens", "total_tokens"):
        if type(value.get(k)) is int and value[k] >= 0:
            result[k] = value[k]
    result["cached_fraction"] = (result["cached_input_tokens"] / result["input_tokens"]
                                  if result["input_tokens"] else None)
    return result


class RolloutReader:
    """Incremental, bounded reader. Cumulative usage is replaced, never summed."""
    def __init__(self, path, thread_id):
        self.path, self.thread_id = Path(path), thread_id
        self.offset, self.inode, self.stamp = 0, None, None
        self.partial = b""
        self.value = {"state": "unknown", "source": "codex_native_rollout"}
        self.usage_key = None
        self.fork_at = None

    def validate_header(self, first):
        meta = first.get("payload", {})
        if first.get("type") != "session_meta" or meta.get("id") != self.thread_id:
            raise ValueError("session_identity_mismatch")
        self.fork_at = timestamp(first.get("timestamp")) if meta.get("forked_from_id") else None

    def poll(self):
        try:
            stat = self.path.stat()
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if stamp == self.stamp:
                return dict(self.value)
            with self.path.open("rb") as stream:
                if self.inode != stat.st_ino or stat.st_size < self.offset:
                    self.value = {"state": "unknown", "source": "codex_native_rollout"}
                    self.offset, self.partial, self.usage_key = 0, b"", None
                    first = json.loads(stream.readline(MAX_READ))
                    self.validate_header(first)
                self.inode = stat.st_ino
                if stat.st_size - self.offset > MAX_READ:
                    self.offset, self.partial = stat.st_size - MAX_READ, b""
                    stream.seek(self.offset)
                    stream.readline()  # skip a possibly incomplete first record
                    self.offset = stream.tell()
                    self.value["history_truncated"] = True
                stream.seek(self.offset)
                data = self.partial + stream.read(MAX_READ)
                self.offset = stream.tell()
                lines = data.split(b"\n")
                self.partial = lines.pop()
                for line in lines:
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    self.accept(row)
            self.stamp = stamp
            self.value["available"] = True
            self.value.pop("unavailable_reason", None)
        except (OSError, ValueError) as exc:
            self.value.update(available=False, state="unknown",
                              unavailable_reason=type(exc).__name__)
        return dict(self.value)

    def accept(self, row):
        at = timestamp(row.get("timestamp"))
        if at is None or (self.fork_at is not None and at <= self.fork_at):
            return  # copied parent history is not this child's new usage/activity
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            return
        if row.get("type") == "turn_context":
            for key in ("model", "effort"):
                if isinstance(payload.get(key), str):
                    self.value[key] = payload[key]
            return
        if row.get("type") != "event_msg":
            return
        kind, turn = payload.get("type"), payload.get("turn_id")
        if kind == "task_started":
            self.value.update(state="running", turn_id=turn, state_at=at)
        elif kind in TERMINAL and (self.value.get("turn_id") in (None, turn)):
            self.value.update(state=TERMINAL[kind], turn_id=turn, state_at=at)
        elif kind == "token_count":
            info = payload.get("info") or {}
            if not isinstance(info, dict):
                return
            last, total = usage_counts(info.get("last_token_usage")), usage_counts(info.get("total_token_usage"))
            if last is None:
                return
            key = json.dumps(info.get("total_token_usage") or info.get("last_token_usage"), sort_keys=True)
            if key == self.usage_key:
                return  # repeated rate-limit events do not refresh usage age
            self.usage_key = key
            self.value["usage"] = dict(last=last, reported_total=total, observed_at=at,
                                       turn_id=self.value.get("turn_id"), source="codex_native_rollout",
                                       cost_known=False, cache_residency="unknown")


def claude_processes(proc_root=None, sessions_root=None, machine_id_path=None):
    """Use native current-session registration, then explicit CLI identity.

    Resuming inside an existing CLI does not change its original argv. The
    native PID/start registration is the authoritative current session in that
    case; stale PID files never override a live process's identity.
    """
    if proc_root is None and sys.platform == "win32":
        return windows_claude_processes()
    proc_root = Path("/proc") if proc_root is None else proc_root
    config_root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()/".claude")
    sessions_root = Path(sessions_root) if sessions_root is not None else config_root/"sessions"
    try:
        host_id = Path(machine_id_path or "/etc/machine-id").read_text().strip()
    except OSError:
        host_id = None
    try:
        boot_time = next(int(line.split()[1]) for line in (proc_root/"stat").read_text().splitlines()
                         if line.startswith("btime "))
    except (OSError, ValueError, StopIteration):
        boot_time = None
    result = {}
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / "comm").read_text().strip()
            if name.lower() not in {"claude", "claude.exe", "node", "node.exe"}:
                continue
            args = [arg for arg in (proc/"cmdline").read_bytes().decode().split("\0") if arg]
            if not is_claude_cli(name, args):
                continue
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] in {"Z", "X"}:
                continue
            pid = int(proc.name)
            domain = None
            if host_id:
                try:
                    domain = "linux:"+host_id+":"+os.readlink(proc/"ns/pid")
                except OSError:
                    pass
            registration = sessions_root/(proc.name+".json")
            record = {}
            if registration.is_file() and registration.stat().st_size <= 65536:
                record = read_json(registration)
            created = boot_time + int(stat[19])/os.sysconf("SC_CLK_TCK") if boot_time is not None else None
            registered = native_claude_session(record, pid, stat[19], domain, created)
            try:
                argument_id = str(uuid.UUID(cli_argument(args, "--session-id", "--resume")))
            except (ValueError, TypeError, AttributeError):
                argument_id = None
            tid = registered or argument_id
            if not tid:
                continue
            # If /resume changed the session, do not reuse the old session's
            # launch model/effort as the resumed session's request settings.
            same_launch = argument_id == tid
            result[tid] = {"pid": pid, "start_ticks": stat[19],
                           "identity_source": "native_session_registration" if registered else "cli_start_arguments",
                           "requested_model": cli_argument(args,"--model") if same_launch else None,
                           "requested_effort": cli_argument(args,"--effort") if same_launch else None,
                           "requested_source": "cli_start_arguments" if same_launch else None}
        except (OSError, ValueError, TypeError, AttributeError, IndexError, UnicodeDecodeError):
            continue
    return result


class ClaudeReader(RolloutReader):
    def validate_header(self, first):
        # Claude's native log has no session_meta header. Every consumed record
        # with a session ID is checked below; file discovery uses exact UUIDs.
        tid = first.get("sessionId") or first.get("session_id")
        if tid and tid != self.thread_id:
            raise ValueError("session_identity_mismatch")
        self.value["source"] = "claude_native_log"

    def accept(self, row):
        tid = row.get("sessionId") or row.get("session_id")
        if (tid and tid != self.thread_id) or row.get("isSidechain"):
            return
        if row.get("type") == "custom-title" and isinstance(row.get("customTitle"), str):
            self.value["title"] = " ".join(row["customTitle"].split())[:160]
        at = timestamp(row.get("timestamp"))
        if at is None:
            return
        if not self.value.get("title") and isinstance(row.get("cwd"), str):
            self.value["title"] = Path(row["cwd"]).name or "Claude 작업"
        if row.get("type") == "user":
            self.value.update(state="running", state_at=at)
        if row.get("type") != "assistant":
            return
        msg = row.get("message") or {}
        if not isinstance(msg, dict):
            return
        model = msg.get("model")
        if isinstance(model, str) and model not in ("<synthetic>", "synthetic"):
            self.value["served"] = model
        effort = row.get("perTurnEffort") or row.get("effort")
        if isinstance(effort, str):
            self.value["recorded_effort"] = effort
        self.value.update(state="completed" if msg.get("stop_reason") == "end_turn" else "running", state_at=at)
        raw = msg.get("usage")
        if not isinstance(raw, dict) or not msg.get("id"):
            return
        keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        if any(type(raw.get(k)) is not int or raw[k] < 0 for k in keys):
            return
        counts = usage_counts({"input_tokens": sum(raw[k] for k in keys[:3]),
                               "cached_input_tokens": raw["cache_read_input_tokens"],
                               "cache_write_input_tokens": raw["cache_creation_input_tokens"],
                               "output_tokens": raw["output_tokens"]})
        key = (msg["id"], json.dumps(counts, sort_keys=True))
        if key == self.usage_key:
            return  # thinking/text blocks of one response share the same usage
        self.usage_key = key
        self.value["usage"] = dict(last=counts, reported_total=None, observed_at=at,
                                   response_id=msg["id"], source="claude_native_log", cost_known=False,
                                   cache_residency="unknown")


class ClaudeCollector:
    def __init__(self, root=None, proc_root=None):
        self.root = Path(root or Path.home() / ".claude/projects")
        self.proc_root, self.readers = proc_root, {}
        self.paths, self.scanned_at = {}, 0

    def snapshot(self, now):
        active = claude_processes(self.proc_root)
        if now - self.scanned_at >= 10:
            candidates = {}
            for path in self.root.glob("*/*.jsonl"):
                try:
                    tid = str(uuid.UUID(path.stem)); mtime = path.stat().st_mtime
                    if tid not in active and now - mtime > 86400:
                        continue
                    if tid not in candidates or mtime > candidates[tid][0]:
                        candidates[tid] = (mtime, path.resolve())
                except (OSError, ValueError):
                    continue
            chosen = sorted(candidates, key=lambda tid: (tid in active, candidates[tid][0]), reverse=True)[:64]
            self.paths = {tid: candidates[tid][1] for tid in chosen}
            self.scanned_at = now
        result = {}
        for tid, path in self.paths.items():
            reader = self.readers.get(tid)
            if reader is None or reader.path != path:
                reader = self.readers[tid] = ClaudeReader(path, tid)
            value = dict(reader.poll(), session_id=tid, provider="anthropic", process_alive=tid in active,
                         **active.get(tid, {}))
            if tid not in active:
                value["state"] = "offline"
            elif value.get("state") == "running" and now - value.get("state_at", 0) > 300:
                value["state"] = "unknown"  # quiet long-running tools are not declared complete
            value["last_model_activity_at"] = (value.get("usage") or {}).get("observed_at")
            result[tid] = value
        for tid, process in active.items():
            if tid not in result:
                result[tid] = dict(process, session_id=tid, provider="anthropic", process_alive=True,
                                   state="unknown", title="Claude 작업", available=False)
        self.readers = {tid: reader for tid, reader in self.readers.items() if tid in self.paths}
        return result


class ActivityCollector:
    def __init__(self, directory=None, database=None):
        self.directory = Path(directory or state_dir())
        self.catalog = ThreadCatalog(database)
        self.readers = {}
        self.candidates_at, self.candidates = 0, []
        self.claude = ClaudeCollector()
        self.snapshots = SnapshotReader()
        self.publisher = Publisher(self.directory)

    def publish(self):
        self.publisher.publish('activity', self.snapshot(), heartbeat=True)

    def claude_roles(self):
        """Explicit role-to-registry bindings survive session rotations, not prompts."""
        bindings = read_json(self.directory / "role-sources.json").get("claude", [])
        roles = {}
        for binding in bindings:
            if not isinstance(binding, dict) or not isinstance(binding.get("id"), str):
                continue
            registry = read_json(Path(binding.get("registry", "")))
            # This adapter reads the existing sole-editor registry; never edits it.
            owner = registry.get("current_execution_redesign") or {}
            tid = owner.get("receiver_session_id")
            try:
                tid = str(uuid.UUID(tid))
            except (ValueError, TypeError, AttributeError):
                tid = None
            roles[binding["id"]] = {"title": binding.get("title") or "구현 담당",
                                    "session_id": tid, "previous_session_id": owner.get("previous_receiver_session_id"),
                                    "source": "sole_editor_registry"}
        return roles

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        capture = self.snapshots.read(self.directory / "desktop.json")
        captured = {s["thread_id"]: s for s in capture.get("sessions", []) if s.get("thread_id")}
        if now - self.candidates_at >= 5:
            try:
                with closing(sqlite3.connect(self.catalog.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                    self.candidates = [r[0] for r in db.execute(
                        "SELECT id FROM threads WHERE COALESCE(archived,0)=0 ORDER BY updated_at DESC LIMIT 128")]
                self.candidates_at = now
            except sqlite3.Error:
                pass
        ids = set(self.candidates) | set(captured)
        self.catalog.resolve(ids)
        threads = {}
        for tid, meta in self.catalog.metadata.items():
            value = {k: v for k, v in meta.items() if k != "rollout_path"}
            path = meta.get("rollout_path")
            if path:
                reader = self.readers.get(tid)
                if reader is None or str(reader.path) != path:
                    reader = self.readers[tid] = RolloutReader(path, tid)
                value.update(reader.poll())
            else:
                value.update(state="unknown", available=False)
            seen = captured.get(tid) or {}
            # Per-request observations, not the publisher's two-second heartbeat.
            stamps = [seen.get("request_started_at"), seen.get("observed_at"),
                      (value.get("usage") or {}).get("observed_at")]
            value["last_model_activity_at"] = max((v for v in stamps if isinstance(v, (int, float))), default=None)
            latest = max(value.get("state_at") or 0, value["last_model_activity_at"] or 0)
            if value.get("state") == "running" and now - latest > 1800:
                value.update(state="unknown", unavailable_reason="no_recent_activity_signal")
            threads[tid] = value
        self.readers = {k: v for k, v in self.readers.items() if k in threads}
        return {"schema": "codex_local_activity_v1", "implementation": "fluff-monitor/"+__version__, "updated_at": now,
                "catalog_available": self.catalog.available, "threads": threads,
                "claude_sessions": self.claude.snapshot(now),
                "claude_roles": self.claude_roles(),
                "model_calls": 0, "cache_residency": "unknown"}


def main(argv=None):
    from .identity import set_process_name
    set_process_name('pawline-usage')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    directory = args.state_dir or state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "activity.lock").open("a+b") as lock:
        if not lock_exclusive(lock):
            return 0
        collector, done = ActivityCollector(directory, args.database), threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: done.set())
        while not done.is_set():
            collector.publish()
            if args.once or done.wait(2):
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
