"""Notification deduplication and animation pacing, independent of GTK."""
from __future__ import annotations

import random
import math
import time
from pathlib import Path

from .storage import atomic_json, read_json, state_dir


class MismatchAlerts:
    """Use cumulative counters so a short mismatch cannot disappear between UI polls.

    When a newer request has already replaced its metadata, report the count without
    pretending the newer response is the mismatched one. State survives pet restarts.
    """
    def __init__(self, path=None):
        self.path = Path(path) if path else state_dir()/"pet-alerts.json"
        saved = read_json(self.path)
        self.epoch = saved.get("capture_pid")
        self.counts = saved.get("counts") or {}
        self.pending = saved.get("pending") or {}
        self.initialized = bool(saved)

    def poll(self, route, now=None):
        now = time.time() if now is None else now
        if not route.get("active") or now-(route.get("updated_at") or 0)>7:
            return []
        epoch = route.get("publisher_pid")
        fresh_epoch = epoch != self.epoch
        counts = {} if fresh_epoch else dict(self.counts)
        pending = {} if fresh_epoch else {tid:n for tid,n in self.pending.items()
                                          if now-n.get("detected_at", 0)<30}
        notices = []
        eligible = {s["thread_id"]:s for s in route.get("sessions") or [] if s.get("thread_id")}
        for entry in route.get("observations", route.get("sessions")) or []:
            tid = entry.get("thread_id")
            if not tid:
                continue
            count = max(0, int(entry.get("historical_mismatches") or 0))
            previous = counts.get(tid)
            detailed = (entry.get("verdict") == "REROUTED" and entry.get("response_id")
                        and entry.get("requested") and entry.get("served")
                        and not entry.get("capture_lost"))
            recent = now-(entry.get("observed_at") or 0)<15
            if previous is None:
                # On first observation, do not replay accumulated historical warnings.
                delta = count if self.initialized and not fresh_epoch else 1 if count and detailed and recent else 0
            else:
                delta = max(0, count-previous)
            if delta and not entry.get("capture_lost"):
                notice = dict(thread_id=tid, count=delta+(pending.get(tid) or {}).get("count", 0),
                              detected_at=now, observed_at=entry.get("observed_at"), detailed=bool(detailed))
                if detailed:
                    notice.update({key:entry.get(key) for key in
                                   ("requested","effort","served","response_id")})
                pending[tid] = notice
            counts[tid] = count
        for tid in list(pending):
            parent_id = (route.get("activity_parent_map") or {}).get(tid)
            if tid in eligible or parent_id in eligible:
                notice = pending.pop(tid)
                notice["title"] = (eligible[tid].get("title") if tid in eligible
                                   else (eligible[parent_id].get("title") or "작업") + " · 하위 활동")
                notices.append(notice)
        if fresh_epoch or counts != self.counts or pending != self.pending or notices or not self.initialized:
            self.epoch, self.counts, self.pending, self.initialized = epoch, counts, pending, True
            atomic_json(self.path, dict(capture_pid=epoch, counts=counts, pending=pending))
        return notices


class MotionCycle:
    """Cycle every supplied animation once per shuffled round at a relaxed pace."""
    PLAYBACK_RATE = .75

    def __init__(self, durations, rng=None):
        self.durations = {name: duration/self.PLAYBACK_RATE for name, duration in durations.items()}
        self.rng = rng or random.Random()
        self.deadline = 0.0
        self.gesture = None
        self.last = None
        self.resting = True
        self.remaining = []

    def sample(self, now):
        if now >= self.deadline:
            if not self.resting:
                self.resting = True
                self.deadline = now+self.rng.uniform(.6, 1.0)
            else:
                if not self.remaining:
                    self.remaining = list(self.durations) or ["idle"]
                    self.rng.shuffle(self.remaining)
                    if len(self.remaining) > 1 and self.remaining[-1] == self.last:
                        self.remaining[0], self.remaining[-1] = self.remaining[-1], self.remaining[0]
                self.gesture = self.remaining.pop()
                self.last, self.resting = self.gesture, False
                duration = max(.1, self.durations.get(self.gesture, 1))
                cycles = math.ceil(self.rng.uniform(2.4, 3.6)/duration)
                self.deadline = now+duration*cycles
        return ("idle", True) if self.resting else (self.gesture, False)
