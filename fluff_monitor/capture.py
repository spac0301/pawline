"""Run the native CLI with bounded, asynchronous metadata observation.

Relay callbacks only enqueue. Parsing/aggregation and private snapshot IO run
on one worker, outside the socket pumps. No provider probes are generated.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import argparse
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import threading
import time

from . import live, proxy, storage
from . import __version__
from .platform_support import native_library_search_path

PUBLISH_INTERVAL = 1.0
MAX_QUEUE_ITEMS = 512
MAX_QUEUE_BYTES = 16 * 1024 * 1024


class DesktopCapture:
    """One bounded inbox and reducer. Loss invalidates old connections, never relay.

    Proxy connection IDs increase monotonically. A loss quarantines IDs already
    seen; a fresh connection can resume observation without restarting the CLI.
    Missing frames on an old connection are never guessed or rebound. Snapshot
    write failures are retried without blocking the socket threads.
    """
    def __init__(self, directory=None, *, publish_interval=PUBLISH_INTERVAL,
                 max_items=MAX_QUEUE_ITEMS, max_bytes=MAX_QUEUE_BYTES):
        self.publisher = storage.Publisher(directory)
        self.agg = live.LiveAggregator()
        self.interval = publish_interval
        self.max_items, self.max_bytes = max_items, max_bytes
        self.condition = threading.Condition()
        self.inbox = deque()
        self.queued_bytes = 0
        self.accepted = self.processed = 0
        self.published = -1
        self.requested_flush = 0
        self.disabled_reason = None
        self.last_connection = self.invalid_through = -1
        self.generation = 0
        self.pending_loss = None
        self.last_observation_loss = None
        self.publication_failures = 0
        self.stopping = False
        self.active = True
        self.worker = threading.Thread(target=self._run, name="fluff-observer", daemon=True)
        self.worker.start()

    def _invalidate_locked(self, reason, incoming_bytes=0):
        """Reserve a loss barrier even when the data inbox is full (lock held)."""
        self.generation += 1
        self.accepted += 1
        self.invalid_through = self.last_connection
        self.disabled_reason = reason
        self.last_observation_loss = dict(
            reason=reason, generation=self.generation, at=time.time(),
            connection_through=self.invalid_through, queue_items=len(self.inbox),
            queue_bytes=self.queued_bytes, incoming_bytes=incoming_bytes)
        self.pending_loss = (self.generation, self.accepted, reason)
        self.inbox.clear()
        self.queued_bytes = 0
        self.condition.notify_all()

    def _enqueue(self, kind, value, size, conn):
        with self.condition:
            if self.stopping or conn <= self.invalid_through:
                return False
            self.last_connection = max(self.last_connection, conn)
            if len(self.inbox) >= self.max_items or self.queued_bytes + size > self.max_bytes:
                self._invalidate_locked("observation_queue_limit", size)
                return False
            self.accepted += 1
            self.inbox.append((self.accepted, kind, value, size))
            self.queued_bytes += size
            self.condition.notify()
            return True

    def feed(self, message):
        # Retain UTF-8 bytes, not a wide Unicode copy of a mostly-ASCII prompt.
        # JSON parsing stays on the worker. Account for the retained buffer plus
        # fixed message/queue overhead rather than charging every character 4x.
        data = message.text.encode("utf-8") if isinstance(message.text, str) else message.text
        return self._enqueue("message", replace(message, text=data), len(data) + 256, message.conn)

    def event(self, kind, info):
        if kind not in ("ws_open", "ws_close", "http_open", "http_close", "parse_lost"):
            return True
        return self._enqueue("event", (kind, dict(info)), 1024, info["conn"])

    def flush(self, timeout=3):
        """Wait for a published observation barrier (diagnostics/tests only)."""
        end = time.monotonic() + timeout
        with self.condition:
            target = self.accepted
            self.requested_flush = max(self.requested_flush, target)
            self.condition.notify()
            while self.published < target:
                if not self.worker.is_alive():
                    return self.published >= target
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.condition.wait(left)
            return True

    def _publish(self):
        value = dict(self.agg.current(), source="desktop", active=self.active,
                     implementation="fluff-monitor/" + __version__,
                     transports=["websocket", "http"], sessions=self.agg.sessions(),
                     session_protocol=1, observation_disabled=self.disabled_reason,
                     last_observation_loss=self.last_observation_loss,
                     publication_failures=self.publication_failures,
                     detail="선택한 Codex 작업의 실제 요청 모델·추론 단계와 서버 응답 모델명")
        self.publisher.publish("desktop", value, heartbeat=True)

    def _run(self):
        next_publish = time.monotonic()
        handled_generation = 0
        while True:
            with self.condition:
                loss = self.pending_loss
                self.pending_loss = None
                stopping = self.stopping
                item = self.inbox.popleft() if self.inbox else None
                if item:
                    self.queued_bytes -= item[3]
                force = self.requested_flush > self.published
            if loss:
                handled_generation, sequence, reason = loss
                self.agg.invalidate_all(reason)
                with self.condition:
                    self.processed = max(self.processed, sequence)
                next_publish = 0
            if item:
                sequence, kind, value, _ = item
                with self.condition:
                    if handled_generation != self.generation:
                        # Another loss occurred while the worker was reducing.
                        # Its reserved barrier covers this discarded item too.
                        continue
                    self.disabled_reason = None
                try:
                    self.agg.resume_observation()
                    if kind == "message":
                        self.agg.feed(value)
                    else:
                        self.agg.connection_event(*value)
                except Exception:
                    with self.condition:
                        self._invalidate_locked("observation_processing_failed")
                finally:
                    with self.condition:
                        self.processed = max(self.processed, sequence)
            with self.condition:
                if handled_generation != self.generation:
                    continue
            now = time.monotonic()
            if stopping:
                self.active = False
            if now >= next_publish or (force and self.processed >= self.requested_flush) or stopping:
                try:
                    self.agg.prune()
                    self._publish()
                except Exception:
                    self.publication_failures += 1
                else:
                    with self.condition:
                        # A loss can arrive while filesystem IO is blocked.
                        # Do not release a barrier for the earlier good snapshot.
                        if handled_generation == self.generation:
                            self.published = self.processed
                            self.condition.notify_all()
                next_publish = time.monotonic() + self.interval
            if stopping:
                return
            with self.condition:
                if not self.inbox and self.pending_loss is None:
                    self.condition.wait(max(0, next_publish - time.monotonic()))

    def close(self):
        with self.condition:
            self.stopping = True
            self.inbox.clear()
            self.queued_bytes = 0
            self.condition.notify_all()
        self.worker.join(timeout=3)


def resolve_real_cli(env):
    """A saved install target also works in the browser's filtered environment."""
    value = env.get("CODEX_ROUTING_REAL_CLI")
    if not value:
        saved = storage.read_json(storage.state_dir() / "capture-config.json")
        value = saved.get("real_cli") if saved.get("schema") == "fluff_capture_config_v1" else None
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise ValueError("Configure the native Codex executable with --fluff-configure-cli PATH or CODEX_ROUTING_REAL_CLI.")
    binary = Path(value).resolve()
    adapter = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "launch-capture.sh"
    if binary == adapter.resolve():
        raise ValueError("The native CLI target must not point back to this adapter.")
    return str(binary)


def configure_cli(argv):
    parser = argparse.ArgumentParser(description="Remember this installation's native Codex CLI; no account credentials are saved.")
    parser.add_argument("cli", type=Path)
    args = parser.parse_args(argv)
    try:
        binary = resolve_real_cli({"CODEX_ROUTING_REAL_CLI": str(args.cli.expanduser().absolute())})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    storage.atomic_json(storage.state_dir() / "capture-config.json",
                        {"schema": "fluff_capture_config_v1", "real_cli": binary})
    print("Native Codex CLI configured. Desktop observation requires FLUFF_DESKTOP_CAPTURE=1.")
    return 0


def run_native_cli(binary, args, env, *, replace_process=False):
    """Keep stdio and exit status attached to the caller on both platforms."""
    if replace_process and sys.platform != "win32":
        return os.execve(binary, [binary, *args], env)
    # Windows execve exits the wrapper before its child finishes. Popen owns
    # the child until completion and handles Windows argv quoting and pipes.
    with native_library_search_path():
        child = subprocess.Popen(
            [binary, *args], env=env, stdin=sys.stdin,
            stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
    previous = {}
    def forward(sig, _):
        if child.poll() is None:
            if sys.platform == "win32" and sig == signal.SIGINT:
                try:
                    child.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:  # A GUI launcher may not have a console.
                    child.terminate()
            else:
                child.send_signal(sig)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, forward)
        return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--fluff-configure-cli"]:
        return configure_cli(args[1:])
    env = dict(os.environ)
    try:
        binary = resolve_real_cli(env)
    except ValueError as exc:
        print("Routing adapter: " + str(exc), file=sys.stderr)
        return 1
    observe_desktop = env.pop("FLUFF_DESKTOP_CAPTURE", None) == "1"
    if not observe_desktop or "app-server" not in args:
        # Browser/config helpers must reach the real CLI and its policy checks
        # without starting another observer or replacing desktop.json.
        return run_native_cli(binary, args, env, replace_process=True)
    if any(env.get(key) for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")):
        print("Fluff capture requires a direct upstream connection. An existing proxy is configured; refusing to overwrite or bypass it.", file=sys.stderr)
        return 1
    ca = proxy.CertAuthority()
    capture = DesktopCapture()
    original_ca = env.get("CODEX_CA_CERTIFICATE")
    upstream_context = ssl.create_default_context(cafile=original_ca) if original_ca else None
    transport = proxy.InterceptProxy(ca, on_message=capture.feed, on_event=capture.event,
        upstream_context=upstream_context,
        intercept_hosts={os.environ.get("CODEX_ROUTING_CAPTURE_HOST", "chatgpt.com")}, require_auth=True)
    try:
        transport.start()
        add, drop = transport.env()
        for key in drop:
            env.pop(key, None)
        env.update(add)
        return run_native_cli(binary, args, env)
    finally:
        transport.stop()
        capture.close()
        ca.close()


if __name__ == "__main__":
    raise SystemExit(main())
