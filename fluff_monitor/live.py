"""Request/response reducer; no probe, launcher or terminal UI."""
from __future__ import annotations
import json,time,uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass,field
from typing import Dict,List,Optional,Tuple
from . import records as cmc,proxy as crp
Event=Tuple[str,dict]

MAX_ROWS = 256
MAX_SESSIONS = 128
MAX_CONNECTIONS = 128
IDLE_RETENTION_SECONDS = 1800

@dataclass
class LiveRow:
    """One server response object seen during the session (the table shows one line per row)."""
    n: int
    conn: int
    first_seen: float
    requested: Optional[str]
    kind: str  # "turn", "warmup" or "error" (a stream error that belongs to no response)
    record: Optional[cmc.ResponseRecord] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    effort: Optional[str] = None
    request_source: str = "request"
    request_n: int = 0
    updated_at: float = 0.0
    thread_id: Optional[str] = None
    counted_verdict: Optional[str] = None
    mismatch_counted: bool = False
    touched: float = field(default_factory=time.monotonic)

    @property
    def response_id(self) -> str:
        return self.record.response_id if self.record else "-"

    @property
    def served(self) -> Optional[str]:
        return self.record.model if self.record else None

    @property
    def status(self) -> Optional[str]:
        return self.record.status if self.record else None

    def verdict(self) -> str:
        if self.record is None:
            return "UNSUPPORTED" if cmc.is_unsupported_error(self.error_code, self.error_message) else "ERROR"
        if not self.requested:
            return "UNKNOWN"
        if self.request_source != "request":
            return "UNKNOWN"  # a connection header is only a hint, not this request's model
        return self.record.verdict(self.requested)

    def other_models(self) -> List[str]:
        if self.record is None or not self.requested:
            return []
        return [m for m in self.record.models_seen if not cmc.models_match(self.requested, m)[0]]


@dataclass
class _Conn:
    collector: cmc.ResponseCollector = field(default_factory=cmc.ResponseCollector)
    pending: List[dict] = field(default_factory=list)
    rows_by_id: Dict[str, LiveRow] = field(default_factory=dict)
    retired: bool = False
    touched: float = field(default_factory=time.monotonic)


def _has_user_input(obj: dict) -> Optional[bool]:
    items = obj.get("input")
    if not isinstance(items, list):
        return None
    return any(isinstance(it, dict) and str(it.get("role", "")).lower() == "user" for it in items)


def request_thread_id(obj: dict) -> Optional[str]:
    """Use the explicit per-request thread ID, not a model, timestamp or cache-key guess."""
    metadata = obj.get("client_metadata")
    value = metadata.get("thread_id") if isinstance(metadata, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


class LiveAggregator:
    """Turns decoded WebSocket messages into LiveRows and summary counts. Not thread-safe:
    feed it from one thread (the window's poll loop, or the command-line loop)."""

    def __init__(self, *, max_rows=MAX_ROWS, max_sessions=MAX_SESSIONS,
                 max_connections=MAX_CONNECTIONS, idle_seconds=IDLE_RETENTION_SECONDS) -> None:
        self.max_rows, self.max_sessions = max_rows, max_sessions
        self.max_connections, self.idle_seconds = max_connections, idle_seconds
        self.rows: List[LiveRow] = []
        self.rate_limits: Optional[dict] = None
        self.conns: Dict[int, _Conn] = OrderedDict()
        self.hints: Dict[int, str] = {}  # conn -> model from the x-codex-routing-hint header
        self.unparsed = 0
        self.request_count = 0
        self.latest_request: Optional[dict] = None
        self.session_requests: Dict[str, dict] = OrderedDict()
        self.rows_by_request: Dict[int, LiveRow] = {}
        self.open_connections: set = set()
        self.lost_connections: set = set()
        self._counts = Counter()
        self._mismatches = Counter()
        self._row_sequence = 0
        self.disabled_reason = None

    def _connection(self, number):
        if number not in self.conns:
            while len(self.conns) >= self.max_connections:
                oldest = next(iter(self.conns))
                self._lose(oldest)
                self._close_connection(oldest)
            self.conns[number] = _Conn()
        conn = self.conns[number]
        conn.touched = time.monotonic()
        self.conns.move_to_end(number)
        return conn

    def _requests(self):
        values = list(self.session_requests.values())
        if self.latest_request is not None and self.latest_request.get("thread_id") not in self.session_requests:
            values.append(self.latest_request)
        return values

    def _lose(self, number):
        if number in self.conns:
            self.lost_connections.add(number)
            self.conns[number].pending.clear()
        for req in self._requests():
            if req["conn"] == number:
                req["capture_lost"] = True

    def _close_connection(self, number):
        self.conns.pop(number, None)
        self.hints.pop(number, None)
        self.open_connections.discard(number)
        self.lost_connections.discard(number)

    def invalidate_all(self, reason):
        self.disabled_reason = reason
        for req in self._requests():
            req["capture_lost"] = True
        self.conns.clear()
        self.hints.clear()
        self.open_connections.clear()
        self.lost_connections.clear()

    def _drop_session(self, tid):
        req = self.session_requests.pop(tid)
        self._mismatches.pop(tid, None)
        if not any(row.request_n == req["n"] for row in self.rows):
            self.rows_by_request.pop(req["n"], None)

    def _drop_row(self, row):
        latest = {req["n"] for req in self._requests()}
        if row.request_n not in latest:
            self.rows_by_request.pop(row.request_n, None)
        conn = self.conns.get(row.conn)
        if conn and row.record:
            conn.retired = True
            conn.rows_by_id.pop(row.response_id, None)
            conn.collector.forget(row.response_id)
            if row.status not in cmc.TERMINAL_STATUSES:
                self._lose(row.conn)

    def _track_verdict(self, row):
        current = row.verdict()
        if current != row.counted_verdict:
            if row.counted_verdict is not None:
                self._counts[row.counted_verdict] -= 1
            self._counts[current] += 1
            row.counted_verdict = current
        if current == "REROUTED" and not row.mismatch_counted:
            if row.thread_id in self.session_requests:
                self._mismatches[row.thread_id] += 1
            row.mismatch_counted = True

    def prune(self):
        now = time.monotonic()
        for number, conn in list(self.conns.items()):
            if now - conn.touched > self.idle_seconds:
                self._lose(number)
                self._close_connection(number)
        for tid, req in list(self.session_requests.items()):
            if now - req["touched"] > self.idle_seconds:
                self._drop_session(tid)
        while len(self.session_requests) > self.max_sessions:
            self._drop_session(next(iter(self.session_requests)))
        while self.rows and (len(self.rows) > self.max_rows or now - self.rows[0].touched > self.idle_seconds):
            self._drop_row(self.rows.pop(0))
        # Index entries may outlive their history row only for a current request.
        retained = {row.request_n for row in self.rows} | {req["n"] for req in self._requests()}
        for number in list(self.rows_by_request):
            if number not in retained:
                self.rows_by_request.pop(number)

    def connection_event(self, name: str, info: dict) -> None:
        conn = info.get("conn")
        if name in ("ws_open", "http_open"):
            self._connection(conn)
            self.open_connections.add(conn)
            self.note_hint(conn, info.get("routing_hint", ""))
        elif name in ("ws_close", "http_close"):
            self._close_connection(conn)
        elif name == "parse_lost":
            self._lose(conn)

    def _bind(self, row: LiveRow, req: dict) -> None:
        row.requested = req["model"]
        row.effort = req["effort"]
        row.request_source = "request"
        row.request_n = req["n"]
        row.thread_id = req.get("thread_id")
        self.rows_by_request[row.request_n] = row
        if req["user"]:
            row.kind = "turn"

    def note_hint(self, conn: int, hint: str) -> None:
        for part in hint.split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip().lower() == "model" and v.strip():
                self.hints[conn] = v.strip()

    def feed(self, msg: crp.WsMessage) -> List[Event]:
        """Return the events this message caused: ("row", {"row": LiveRow, "new": bool}),
        ("rate_limits", {...})."""
        if self.disabled_reason:
            return []
        try:
            obj = json.loads(msg.text)
        except ValueError:
            self.unparsed += 1
            return []
        if not isinstance(obj, dict):
            self.unparsed += 1
            return []
        conn = self._connection(msg.conn)
        # Receiving bytes is fresh liveness evidence even after an idle entry
        # was pruned while the underlying pooled socket remained open.
        self.open_connections.add(msg.conn)
        if msg.direction == "c2s":
            if obj.get("type") == "response.create":
                self.request_count += 1
                reasoning = obj.get("reasoning") or {}
                req = {"n": self.request_count, "conn": msg.conn, "ts": msg.ts,
                       "transport": msg.transport,
                       "model": cmc.metadata_text(obj.get("model")),
                       "effort": cmc.metadata_text(reasoning.get("effort")) if isinstance(reasoning, dict) else None,
                       "user": _has_user_input(obj), "thread_id": request_thread_id(obj),
                       "touched": time.monotonic(), "capture_lost": msg.conn in self.lost_connections}
                self.latest_request = req
                if req["thread_id"]:
                    self.session_requests[req["thread_id"]] = req
                    self.session_requests.move_to_end(req["thread_id"])
                # Missing/out-of-order capture cannot safely be repaired by borrowing a
                # previous request or binding an already observed response to a later one.
                inflight = any(rec.status not in cmc.TERMINAL_STATUSES and not rec.error_code
                               for rec in conn.collector.by_id.values())
                if conn.pending or inflight:
                    # Without an acknowledged response identity, a second request
                    # makes FIFO association ambiguous. Preserve uncertainty.
                    self._lose(msg.conn)
                    req["capture_lost"] = True
                elif msg.conn not in self.lost_connections:
                    conn.pending.append(req)
                self.prune()
            return []
        events: List[Event] = []
        if obj.get("type") == "codex.rate_limits":
            self.rate_limits = obj
            events.append(("rate_limits", {"rate_limits": obj}))
        rec, stream_error = conn.collector.feed(obj)
        if rec is not None:
            row = conn.rows_by_id.get(rec.response_id)
            new = row is None
            if row is None:
                kind = rec.kind
                self._row_sequence += 1
                row = LiveRow(n=self._row_sequence, conn=msg.conn, first_seen=msg.ts, requested=None,
                              kind=kind, record=rec, request_source="unknown")
                if conn.retired and msg.transport == "websocket" and obj.get("type") != "response.created":
                    self._lose(msg.conn)
                if conn.pending and msg.conn not in self.lost_connections:
                    self._bind(row, conn.pending.pop(0))
                else:
                    self._lose(msg.conn)
                    if self.hints.get(msg.conn):
                        row.requested = self.hints[msg.conn]
                        row.request_source = "connection_hint"
                conn.rows_by_id[rec.response_id] = row
                self.rows.append(row)
            row.updated_at = msg.ts
            row.touched = time.monotonic()
            req = self.session_requests.get(row.thread_id)
            if req and req["n"] == row.request_n:
                req["touched"] = time.monotonic()
            self._track_verdict(row)
            events.append(("row", {"row": row, "new": new}))
        elif stream_error:
            code, message = conn.collector.stream_errors[-1]
            self._row_sequence += 1
            row = LiveRow(n=self._row_sequence, conn=msg.conn, first_seen=msg.ts,
                          requested=None, kind="error", updated_at=msg.ts,
                          error_code=code, error_message=message)
            if conn.pending:
                self._bind(row, conn.pending.pop(0))
                row.kind = "error"
            self.rows.append(row)
            self._track_verdict(row)
            events.append(("row", {"row": row, "new": True}))
        if events and any(info.get("new") for kind, info in events if kind == "row"):
            self.prune()
        return events

    # summary
    def counts(self) -> Dict[str, int]:
        return {key: value for key, value in self._counts.items() if value}

    def pairs(self) -> List[str]:
        seen = []
        for r in self.rows:
            if r.verdict() == "REROUTED":
                p = f"{r.requested} -> {', '.join(sorted(set(r.other_models())))}"
                if p not in seen:
                    seen.append(p)
        return seen

    def overall(self) -> str:
        """Historical summary. Current request state is exposed separately by current()."""
        pairs = [(r.kind, r.verdict()) for r in self.rows]
        verdicts = [v for _k, v in pairs]
        if "REROUTED" in verdicts:
            return "REROUTED"
        if "UNSUPPORTED" in verdicts:
            return "UNSUPPORTED"
        if "ERROR" in verdicts or "UNKNOWN" in verdicts:
            return "ERROR"
        if "ok" in verdicts:
            return "OK"
        return "NO_DATA"

    def current(self, thread_id: Optional[str] = None) -> dict:
        """Small diagnostic state for the latest request; never reuse an earlier success."""
        req = self.session_requests.get(thread_id) if thread_id else self.latest_request
        row = self.rows_by_request.get(req["n"]) if req else None
        if req is None and self.rows and thread_id is None:
            row = self.rows[-1]
        observed = row.updated_at if row else (req["ts"] if req else None)
        result = {"thread_id": req.get("thread_id") if req else thread_id,
                  "transport": req.get("transport", "websocket") if req else None,
                  "request_started_at": req["ts"] if req else None,
                  "requested": req["model"] if req else (row.requested if row else None),
                  "effort": req["effort"] if req else None,
                  "served": row.served if row else None, "observed_at": observed,
                  "response_id": row.response_id if row else None,
                  "request_source": row.request_source if row else ("request" if req else "unknown"),
                  "status": row.status if row else None,
                  "error_code": (row.error_code or (row.record.error_code if row.record else None)) if row else None,
                  "verdict": row.verdict() if row else ("PENDING" if req else "NO_DATA")}
        if row and row.record and row.status in (None, "queued", "in_progress") and result["verdict"] != "REROUTED":
            result["verdict"] = "PENDING"
        conn = req["conn"] if req else (row.conn if row else None)
        lost = self.disabled_reason is not None or conn in self.lost_connections or bool(req and req.get("capture_lost"))
        if lost:
            result["verdict"] = "UNKNOWN"
            result["served"] = None
        result["connected"] = conn in self.open_connections if conn is not None else bool(self.open_connections)
        result["capture_lost"] = lost
        result["historical_mismatches"] = self._mismatches.get(thread_id, 0) if thread_id else self._counts.get("REROUTED", 0)
        return result

    def sessions(self) -> List[dict]:
        ids = sorted(self.session_requests, key=lambda tid: self.session_requests[tid]["n"], reverse=True)
        return [self.current(tid) for tid in ids]
