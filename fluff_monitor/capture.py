"""Run the native CLI with bounded, asynchronous metadata observation.

Relay callbacks only enqueue. Parsing/aggregation and private snapshot IO run
on one worker, outside the socket pumps. No provider probes are generated.
"""
from __future__ import annotations

from collections import deque
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

PUBLISH_INTERVAL = 1.0
MAX_QUEUE_ITEMS = 512
MAX_QUEUE_BYTES = 16 * 1024 * 1024


class DesktopCapture:
    """One bounded inbox and reducer. Overload disables observation, never relay.

    Once an event is lost, this capture instance stays explicitly unobserved.
    It cannot guess the missing request/response association. A normal adapter
    restart creates a new instance. Snapshot write failures alone are retried
    at the publication interval without blocking the socket threads.
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
        self.publication_failures = 0
        self.stopping = False
        self.active = True
        self.worker = threading.Thread(target=self._run, name="fluff-observer", daemon=True)
        self.worker.start()

    def _enqueue(self, kind, value, size):
        with self.condition:
            if self.stopping or self.disabled_reason:
                return False
            if len(self.inbox) >= self.max_items or self.queued_bytes + size > self.max_bytes:
                self.disabled_reason = "observation_queue_limit"
                self.inbox.clear()
                self.queued_bytes = 0
                self.condition.notify_all()
                return False
            self.accepted += 1
            self.inbox.append((self.accepted, kind, value, size))
            self.queued_bytes += size
            self.condition.notify()
            return True

    def feed(self, message):
        # Four bytes per codepoint is a conservative bound without copying the
        # whole request into another UTF-8 buffer on the socket thread.
        return self._enqueue("message", message, 4 * len(message.text) + 256)

    def event(self, kind, info):
        if kind not in ("ws_open", "ws_close", "http_open", "http_close", "parse_lost"):
            return True
        return self._enqueue("event", (kind, dict(info)), 1024)

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
                     publication_failures=self.publication_failures,
                     detail="선택한 Codex 작업의 실제 요청 모델·추론 단계와 서버 응답 모델명")
        self.publisher.publish("desktop", value, heartbeat=True)

    def _run(self):
        next_publish = time.monotonic()
        invalidated = False
        while True:
            with self.condition:
                disabled = self.disabled_reason
                stopping = self.stopping
                item = self.inbox.popleft() if self.inbox and not disabled else None
                if item:
                    self.queued_bytes -= item[3]
                force = self.requested_flush > self.published
            if disabled and not invalidated:
                self.agg.invalidate_all(disabled)
                invalidated = True
                with self.condition:
                    self.processed = self.accepted
                next_publish = 0
            if item:
                sequence, kind, value, _ = item
                try:
                    if kind == "message":
                        self.agg.feed(value)
                    else:
                        self.agg.connection_event(*value)
                except Exception:
                    with self.condition:
                        self.disabled_reason = "observation_processing_failed"
                        self.inbox.clear()
                        self.queued_bytes = 0
                finally:
                    with self.condition:
                        self.processed = max(self.processed, sequence)
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
                        if not (self.disabled_reason and not invalidated):
                            self.published = self.processed
                            self.condition.notify_all()
                next_publish = time.monotonic() + self.interval
            if stopping:
                return
            with self.condition:
                if not self.inbox and not (self.disabled_reason and not invalidated):
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
        return os.execve(binary, [binary, *args], env)
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
    child = None
    try:
        transport.start()
        add, drop = transport.env()
        for key in drop:
            env.pop(key, None)
        env.update(add)
        child = subprocess.Popen([binary, *args], env=env, stdin=sys.stdin,
                                 stdout=sys.stdout, stderr=sys.stderr)

        def forward(sig, _):
            if child.poll() is None:
                child.send_signal(sig)

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, forward)
        return child.wait()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        transport.stop()
        capture.close()
        ca.close()


if __name__ == "__main__":
    raise SystemExit(main())
