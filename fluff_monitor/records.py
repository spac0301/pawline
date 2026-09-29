"""Pure interpretation of returned response metadata, not hidden engine identity."""
from __future__ import annotations
from dataclasses import dataclass,field
import re
import math
from typing import Dict,List,Optional,Tuple
TERMINAL_STATUSES={"completed","failed","incomplete","cancelled"}
UNSUPPORTED_RE=re.compile(r"(?i)not supported|not available|unsupported model|does not have access|model_not_found|no access to|not entitled|not enabled for")

def metadata_text(value, limit=256):
    """Reject oversized/non-text identities instead of retaining arbitrary data."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("invalid metadata text")
    return value

def is_unsupported_error(code: Optional[str], message: Optional[str]) -> bool:
    text = f"{code or ''} {message or ''}"
    return bool(UNSUPPORTED_RE.search(text)) and ("model" in text.lower() or "access" in text.lower())


def models_match(requested: str, served: Optional[str]) -> Tuple[bool, Optional[str]]:
    """(match, note). A dated snapshot of the requested model counts as a match."""
    if not served:
        return False, None
    r, s = requested.strip().lower(), served.strip().lower()
    if r == s:
        return True, None
    if re.fullmatch(re.escape(r) + r"-\d{4}-\d{2}-\d{2}", s):  # exactly `<requested>-YYYY-MM-DD`
        return True, f"served a dated snapshot of {requested}: {served}"
    return False, None


@dataclass
class ResponseRecord:
    """One server response object as seen in the stream."""
    response_id: str
    model: Optional[str]
    status: Optional[str]
    service_tier: Optional[str]
    created_at: Optional[int]
    kind: str  # "warmup" (no previous_response_id) or "turn"
    models_seen: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    usage: Optional[dict] = None

    def verdict(self, requested: str) -> str:
        if is_unsupported_error(self.error_code, self.error_message):
            return "UNSUPPORTED"
        # A response object naming another model is evidence even if the response then failed.
        if any(not models_match(requested, m)[0] for m in self.models_seen):
            return "REROUTED"
        if self.error_code or self.status == "failed":
            return "ERROR"
        if not self.models_seen or self.status != "completed":
            return "UNKNOWN"  # no model field, or the response never completed (timeout, crash)
        return "ok"

    def notes(self, requested: str) -> List[str]:
        out = []
        if len(self.models_seen) > 1:
            out.append("model changed mid-response: " + " -> ".join(self.models_seen))
        for m in self.models_seen:
            ok, note = models_match(requested, m)
            if ok and note:
                out.append(note)
        return out


class ResponseCollector:
    """Builds ResponseRecords from server frames, one frame at a time (the live monitor feeds
    frames as they arrive; collect_responses() feeds a whole capture)."""

    def __init__(self) -> None:
        self.by_id: Dict[str, ResponseRecord] = {}
        self.order: List[str] = []
        self.stream_errors: List[Tuple[Optional[str], Optional[str]]] = []

    @property
    def records(self) -> List[ResponseRecord]:
        return [self.by_id[i] for i in self.order]

    def forget(self, response_id):
        self.by_id.pop(response_id, None)
        if response_id in self.order:
            self.order.remove(response_id)

    def feed(self, f: dict) -> Tuple[Optional[ResponseRecord], bool]:
        """Apply one frame. Returns (the record it touched or None, True if a stream error
        that belongs to no response was added)."""
        t = str(f.get("type", ""))
        r = f.get("response")
        if isinstance(r, dict) and r.get("id"):
            rid = metadata_text(r["id"])
            rec = self.by_id.get(rid)
            if rec is None:
                rec = ResponseRecord(
                    response_id=rid, model=None, status=None,
                    service_tier=r.get("service_tier"),
                    created_at=(r.get("created_at") if type(r.get("created_at")) in (int, float)
                                and math.isfinite(r["created_at"]) else None),
                    kind="turn" if r.get("previous_response_id") else "warmup",
                )
                self.by_id[rid] = rec
                self.order.append(rid)
            model = r.get("model")
            if model:
                model = metadata_text(model)
                rec.model = model
                if model not in rec.models_seen:
                    if len(rec.models_seen) >= 32:
                        raise ValueError("response model history exceeds observation bound")
                    rec.models_seen.append(model)
            rec.status = metadata_text(r.get("status")) or rec.status
            rec.service_tier = metadata_text(r.get("service_tier")) or rec.service_tier
            if rec.created_at is None and r.get("created_at") is not None:
                value = r.get("created_at")
                if type(value) in (int, float) and math.isfinite(value):
                    rec.created_at = value
            # Token usage is collected from native logs by activity.py. Do not
            # retain an unused, provider-controlled usage object in this reducer.
            err = r.get("error")
            if isinstance(err, dict) and (err.get("code") or err.get("message")):
                rec.error_code = metadata_text(err.get("code") or err.get("type")) or rec.error_code
                rec.error_message = metadata_text(err.get("message"), 4096) or rec.error_message
            return rec, False
        if t == "error":
            e = f.get("error") if isinstance(f.get("error"), dict) else {}
            code = metadata_text(e.get("code") or e.get("type"))
            msg = metadata_text(e.get("message") or f.get("message"), 4096)
            target = self.by_id[self.order[-1]] if self.order else None
            if target is not None and target.status not in TERMINAL_STATUSES and not target.error_code:
                target.error_code, target.error_message = code, msg
                return target, False
            if target is not None and target.error_code and target.error_code == code:
                return None, False  # the same failure reported twice (response.failed followed by an `error` frame)
            self.stream_errors.append((code, msg))
            del self.stream_errors[:-32]
            return None, True
        return None, False
