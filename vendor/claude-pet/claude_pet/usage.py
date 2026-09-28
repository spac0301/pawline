"""Reading Claude Code's own usage figures: 5-hour and weekly limits, and cost.

None of this is computed here, and none of it can be. The percentages are the
server's own accounting -- it knows your plan's caps, returns how much of each
window you have spent on every request, and Claude Code carries the latest
into the JSON it hands its `statusLine` command on every render. That JSON is
the only place the figures appear: the hooks never see them, and the
transcripts do not carry them, so there is nothing to recompute from. The job
here is only to catch that JSON and read it back.

    "rate_limits": {
        "five_hour": {"used_percentage": 14, "resets_at": 1787218800},
        "seven_day": {"used_percentage": 9,  "resets_at": 1787641200}
    },
    "cost": {"total_cost_usd": 16.24, ...}

Two ways in, tried in that order:

- Our own `statusline` command, if it is installed, writes each session's JSON
  to `usage/<session>.json` here in the state dir.
- Otherwise oh-my-claudecode, if the user runs it, already caches the very
  same JSON to `~/.claude/hud/cache/stdin.*.json`. Same schema, so it is read
  directly rather than asking the user to displace their status line for ours.

The 5-hour and weekly figures are per *account*, so the freshest single file
wins. Cost is per session, and reported as such.
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path
from typing import Any


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "claude-pet"


def cache_dir() -> Path:
    """Where our own statusline capture writes, one file per session."""
    return _state_dir() / "usage"


def _claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def _omc_cache_glob() -> str:
    return str(_claude_config_dir() / "hud" / "cache" / "stdin.*.json")


def extract(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the figures we show out of a statusLine JSON, or None if absent.

    Absent is the ordinary case for the first render of a fresh session, before
    the server has reported a limit -- so it is not an error, just nothing yet.
    """
    if not isinstance(payload, dict):
        return None
    limits = payload.get("rate_limits") or {}
    five = limits.get("five_hour") or {}
    week = limits.get("seven_day") or {}
    cost = payload.get("cost") or {}
    five_pct = five.get("used_percentage")
    week_pct = week.get("used_percentage")
    if five_pct is None and week_pct is None:
        return None
    return {
        "five_hour_pct": _round(five_pct),
        "seven_day_pct": _round(week_pct),
        "five_hour_resets_at": _int(five.get("resets_at")),
        "seven_day_resets_at": _int(week.get("resets_at")),
        "cost_usd": cost.get("total_cost_usd"),
        "session_id": payload.get("session_id"),
        "model": (payload.get("model") or {}).get("display_name"),
    }


def _round(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def capture(raw: str) -> dict[str, Any] | None:
    """Parse a statusLine stdin blob and store what we show. Returns it, or None.

    On the statusLine hot path, so it does the least it can and never raises:
    a broken write must not take down the one line Claude Code is waiting to
    print.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
    figures = extract(payload)
    if figures is None:
        return None
    figures["at"] = time.time()
    session = str(figures.get("session_id") or "default")
    session = "".join(c for c in session if c.isalnum() or c in "-_") or "default"
    try:
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{session}.json"
        tmp = directory / f".{session}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(figures), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        return figures
    return figures


def _load(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    # Our own files are already normalised (they have "at"); OMC's are the raw
    # statusLine JSON and need extracting.
    if "five_hour_pct" in payload:
        return payload
    figures = extract(payload)
    if figures is not None:
        try:
            figures["at"] = os.path.getmtime(path)
        except OSError:
            figures["at"] = 0.0
    return figures


def read() -> dict[str, Any] | None:
    """The freshest usage figures from either source, or None if there are none.

    Freshest by file time, because the 5-hour and weekly percentages belong to
    the account, not a session: the most recently written is the most current,
    whichever session or tool wrote it.
    """
    candidates: list[tuple[float, str]] = []
    our_dir = cache_dir()
    if our_dir.is_dir():
        for path in glob.glob(str(our_dir / "*.json")):
            try:
                candidates.append((os.path.getmtime(path), path))
            except OSError:
                continue
    for path in glob.glob(_omc_cache_glob()):
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError:
            continue
    for _mtime, path in sorted(candidates, reverse=True):
        figures = _load(path)
        if figures is not None:
            return figures
    return None
