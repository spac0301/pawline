"""Bounded response reducer, run only inside the replaceable observer process."""
from __future__ import annotations
from collections import deque
from dataclasses import replace
import threading
import time
from . import live, storage
from . import __version__

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
                 max_items=MAX_QUEUE_ITEMS, max_bytes=MAX_QUEUE_BYTES, checkpoint=None):
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
        self.handoff_pause = False
        self.active = True
        if checkpoint:
            self.agg.restore(checkpoint['aggregator'])
            for key in ('last_connection', 'invalid_through', 'generation',
                        'disabled_reason', 'last_observation_loss'):
                setattr(self, key, checkpoint[key])
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
        handled_generation = self.generation
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
            if stopping and self.handoff_pause:
                return
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

    def handoff(self):
        """Quiesce at a processed boundary; never serialize queued raw messages."""
        if not self.flush():
            raise TimeoutError('observer did not drain before replacement')
        self.close(for_handoff=True)
        if self.worker.is_alive():
            raise TimeoutError('observer did not pause before replacement')
        return dict(aggregator=self.agg.checkpoint(),
            **{key: getattr(self, key) for key in ('last_connection', 'invalid_through',
                'generation', 'disabled_reason', 'last_observation_loss')})

    def close(self, *, for_handoff=False):
        with self.condition:
            self.handoff_pause = for_handoff
            self.stopping = True
            self.inbox.clear()
            self.queued_bytes = 0
            self.condition.notify_all()
        self.worker.join(timeout=3)

