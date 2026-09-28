"""Shared state between the Claude Code hooks and the overlay.

Hooks run on every tool call, so writes have to be cheap and must never block
Claude Code: this module is stdlib-only and every write is an atomic replace
guarded by a short-lived lock file.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

#: Pet states the overlay knows how to render, most urgent first. The names
#: match the animation rows of the Codex pet contract.
PRIORITY: tuple[str, ...] = (
    "waiting",   # Claude is blocked on the user -- the one we most want seen
    "failed",
    "review",    # turn finished, output is waiting to be read
    "running",
    "waving",
    "idle",
)

#: Sessions quieter than this are treated as gone.
SESSION_TTL_SECONDS = 6 * 60 * 60

#: How long a state stays interesting *for the session that reported it*.
#: Past this, that session counts as idle when aggregating, so a finished
#: session announces itself and then stops speaking for the others.
#:
#: `waiting` is deliberately absent: a pet that stops asking for you defeats
#: the point. `running` has a deliberately generous one -- it is a backstop,
#: not a mechanism. Claude normally leaves `running` by reporting Stop, but any
#: event arriving after a turn ends would otherwise re-arm it forever, so this
#: guarantees the pet unsticks itself. Long enough that a slow think between
#: tool calls does not read as idle.
#: Claude's idle nudge, which is not a request for anything. Mirrors
#: `hook.IDLE_NOTIFICATION`; kept here too so `state` stays import-light.
IDLE_NUDGE_TEXT = "waiting for your input"

DWELL_SECONDS: dict[str, float] = {
    "waving": 3.0,
    "review": 20.0,
    "failed": 20.0,
    "running": 300.0,
}


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "claude-pet"


def state_path() -> Path:
    return state_dir() / "state.json"


def pid_path() -> Path:
    return state_dir() / "overlay.pid"


def _empty() -> dict[str, Any]:
    return {"version": 1, "updated": 0.0, "sessions": {}}


def read() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return _empty()
    return data


def _write(data: dict[str, Any]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, state_path())
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


#: `comm` of the Claude Code process.
CLAUDE_COMM = "claude"

#: `any_agent_running()` is a /proc sweep, so its answer is reused briefly.
_SCAN_TTL_SECONDS = 3.0
_scan_cache: tuple[float, bool] | None = None


def _comm_of(pid: str | int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def any_agent_running() -> bool:
    """Whether any coding agent this pet follows exists on this machine at all.

    The backstop for sessions with no recorded pid -- entries written before
    pids were recorded, which would otherwise be trusted for the whole TTL and
    keep the pet alive forever. Errs towards True: never kill the pet on a
    failed guess.

    It asked for a *Claude* process for as long as Claude was the only agent,
    and stayed that way after Codex and Gemini were added. So on a machine
    running Codex and no Claude, every session with no pid was judged dead the
    moment it was written -- reported as the pet not noticing Codex at all,
    which was very nearly the truth.
    """
    global _scan_cache
    now = time.monotonic()
    if _scan_cache is not None and now - _scan_cache[0] < _SCAN_TTL_SECONDS:
        return _scan_cache[1]

    from . import agents

    # Asked of the registry: recognising an agent is its business, and
    # Gemini's process is called `node`, so a sweep for names alone would let
    # any Node program on the machine pass for a live session.
    found = agents.any_running()
    _scan_cache = (now, found)
    return found


#: A `running` session whose process shows no sign of work for this long is
#: treated as finished, well before the blunt backstop above.
#:
#: `Stop` is the clean way out of `running`, but it does not always come: an
#: interrupted turn ends without one, and then the pet sat on "working" for the
#: full five minutes with nothing running. Long enough to ride out a pause
#: between tool calls, short enough that a stale `running` heals while you are
#: still looking at it.
IDLE_CPU_SECONDS = 45.0

#: CPU seconds per wall second below which a Claude process is doing nothing.
#: Measured: a session sitting at its prompt idles at 0.002-0.008, one mid-turn
#: runs at ~0.17. The gap is that wide because Claude Code animates a spinner
#: while it works, so "burning CPU" and "showing itself as busy" are the same
#: thing -- which is exactly the question being asked.
IDLE_CPU_RATE = 0.02

#: How often the overlay records what each session's process has been doing.
#: A rate needs two readings, and a wide gap makes the rate steadier -- there
#: is no hurry, since nothing is judged until a state is already stale.
CPU_SAMPLE_SECONDS = 10.0

_CLOCK_TICKS = float(os.sysconf("SC_CLK_TCK") or 100)


def _cpu_of(pid: int) -> float | None:
    """Total CPU seconds this process has used, or None if it is gone."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
            # The comm field can contain spaces and brackets, so split after it.
            fields = stream.read().rsplit(") ", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / _CLOCK_TICKS
    except (OSError, IndexError, ValueError):
        return None


def sample_cpu() -> None:
    """Record what each live session's process has been up to.

    Written into the state file rather than kept in the overlay's memory, so
    that `claude-pet status` reaches the same verdict as the pet and a
    restarted overlay does not forget what it had already observed.

    `busy_at` is the last moment a session was known to be doing something --
    set here when its process burns CPU, and by `update` on any hook event,
    since an arriving event is itself proof of work.
    """
    with _locked():
        data = read()
        now = time.time()
        changed = False

        for session in data.get("sessions", {}).values():
            if not isinstance(session, dict):
                continue
            locator = session.get("locator")
            pid = locator.get("claude_pid") if isinstance(locator, dict) else None
            if not isinstance(pid, int):
                continue

            sampled_at = session.get("cpu_at")
            if isinstance(sampled_at, (int, float)) and now - sampled_at < CPU_SAMPLE_SECONDS:
                continue

            total = _cpu_of(pid)
            if total is None:
                continue

            previous = session.get("cpu_total")
            if isinstance(previous, (int, float)) and isinstance(sampled_at, (int, float)):
                rate = (total - previous) / max(now - sampled_at, 1e-6)
                if rate >= IDLE_CPU_RATE:
                    session["busy_at"] = now
            else:
                # First sighting proves nothing either way, so assume work.
                session["busy_at"] = now

            session["cpu_total"] = total
            session["cpu_at"] = now
            changed = True

        if changed:
            data["updated"] = now
            _write(data)


def is_alive(session: dict[str, Any], now: float) -> bool:
    """Whether a session should still be counted.

    `SessionEnd` is the clean signal, but it does not arrive when Claude is
    killed outright -- a terminal closed with its window button, a reboot, a
    crash. Without a second check those sessions would linger for the whole TTL
    and the pet would never notice everything had gone away. So the recorded
    Claude pid is verified directly: if that process is gone, so is the session.
    """
    locator = session.get("locator")
    pid = locator.get("claude_pid") if isinstance(locator, dict) else None

    if isinstance(pid, int):
        # Which agent it is decides how to recognise it: `claude` is a process
        # called `claude`, `gemini` is a process called `node` and has to be
        # told apart by its command line. Claude Desktop's own entry names its
        # `comm` outright, having no agent behind it.
        agent = locator.get("agent")
        if isinstance(agent, str) and agent:
            from . import agents

            # Guards against the pid being reused by something unrelated
            # between the session dying and us looking at it.
            return agents.is_process(agent, pid)

        recorded = locator.get("comm")
        # Age deliberately says nothing here. A session sitting at its prompt
        # overnight is idle, not gone, and the TTL used to drop it anyway --
        # taking the pet with it if it was the last one, and leaving it dead
        # until a brand new session was started.
        if isinstance(recorded, str) and recorded:
            return _comm_of(pid) == recorded

        # No agent and no comm: an entry written by a version that recorded
        # neither. Any agent will do rather than Claude alone -- insisting on
        # Claude declared a live Codex session dead, which is the reverse of
        # what a pid check is for.
        from . import agents

        running = _comm_of(pid)
        if not running:
            return False
        return any(
            running == spec["comm"] for spec in agents.AGENTS.values() if spec.get("comm")
        ) or running == CLAUDE_COMM

    # No pid recorded, which is the case for entries written before pids were.
    # Nothing better to go on than age and whether any Claude exists at all:
    # trusting them outright kept the pet alive long after everything closed.
    stamps = [
        value
        for value in (session.get("ts"), session.get("seen"))
        if isinstance(value, (int, float))
    ]
    if not stamps or now - max(stamps) > SESSION_TTL_SECONDS:
        return False
    return any_agent_running()


#: The old name, kept because it is what the tests and any outside caller
#: know. It never meant only Claude in spirit, only in code.
def any_claude_running() -> bool:
    return any_agent_running()


def _prune(sessions: dict[str, Any], now: float) -> None:
    for session_id, session in list(sessions.items()):
        if not isinstance(session, dict) or not is_alive(session, now):
            sessions.pop(session_id, None)


@contextlib.contextmanager
def _locked() -> Any:
    """Hold the state lock, stealing it if the holder appears to be gone.

    A stuck lock must never wedge a Claude turn, so the wait has a hard
    timeout and gives up in favour of writing anyway.
    """
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "state.lock"

    deadline = time.monotonic() + 1.0
    acquired = False
    while True:
        try:
            file_descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(file_descriptor)
            acquired = True
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)

    try:
        yield
    finally:
        if acquired or lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass


def update(session_id: str, **fields: Any) -> dict[str, Any]:
    """Merge `fields` into `session_id`'s entry and persist.

    A `state` of None removes the session.
    """
    with _locked():
        data = read()
        sessions = data["sessions"]
        now = time.time()
        _prune(sessions, now)

        if fields.get("state", "") is None:
            sessions.pop(session_id, None)
        elif "state" not in fields and session_id not in sessions:
            # "Leave the state as it was" for a session there is no record of.
            # There is nothing to leave alone, and inventing a stateless entry
            # only produces one that reads as idle and has lost its history.
            pass
        else:
            session = sessions.get(session_id) or {}
            session.update({key: value for key, value in fields.items() if value is not None})
            session["ts"] = now
            session["seen"] = now
            # A hook event is itself proof the session is doing something, and
            # a far better one than sampling ever gives.
            session["busy_at"] = now
            sessions[session_id] = session

        data["updated"] = now
        _write(data)
        return data


def touch(session_id: str) -> None:
    """Confirm a session still exists without re-reporting what it is doing.

    Liveness and dwell both used to be read off `ts`, so keeping a long-lived
    entry alive re-armed its dwell too and a one-off announcement replayed
    forever -- the Claude Desktop entry, refreshed on a timer, would resurrect
    a `needs you` from an hour earlier every few minutes. `seen` is the
    liveness clock; `ts` stays the moment the state was actually reported.
    """
    with _locked():
        data = read()
        session = data["sessions"].get(session_id)
        if not isinstance(session, dict):
            return
        session["seen"] = data["updated"] = time.time()
        _write(data)


def log_path() -> Path:
    return state_dir() / "transitions.log"


#: Kept small on purpose: this is for answering "why did it just say that",
#: not for keeping history.
LOG_LINES = 200


def log_transition(previous: str, current: str, snapshot: dict[str, Any]) -> None:
    """Record a change in what the pet is showing, and what caused it.

    A wrong state is almost always gone by the time anyone thinks to look, so
    asking the live state file is asking too late. This is the difference
    between diagnosing the next surprise and trying to reproduce it.
    """
    winner = snapshot.get("winner") or {}
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    detail = str(snapshot.get("detail") or "")

    # A change has two possible causes, and naming the wrong one is worse than
    # naming none: when a dwell runs out the last event is whatever came
    # before, so reporting it reads as though that event caused this.
    stored = winner.get("stored")
    if stored and stored != current:
        cause = f"{stored} expired"
    else:
        cause = str(winner.get("event") or "?")

    line = (
        f"{stamp}  {previous} -> {current}"
        f"  via={cause}"
        f"  session={str(winner.get('id') or '?')[:14]}"
        f"  sessions={snapshot.get('sessions', 0)}"
        + (f"  detail={detail!r}" if detail else "")
    )
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        existing.append(line)
        path.write_text("\n".join(existing[-LOG_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass  # a diagnostic must never be the thing that breaks


def effective_state(session: dict[str, Any], now: float) -> str:
    """The state this session still deserves to speak for, given its dwell."""
    reported = session.get("state")
    if reported not in PRIORITY:
        return "idle"

    # A session may carry its own dwell. Claude Desktop's does: its states come
    # from one-off notifications, so nothing will ever arrive to clear them and
    # even `waiting` -- which a real session holds indefinitely, because it
    # keeps re-reporting it -- has to time out on its own.
    dwell = session.get("dwell")
    if not isinstance(dwell, (int, float)):
        dwell = DWELL_SECONDS.get(reported)

    age = now - session.get("ts", 0.0)
    if dwell is not None and age > dwell:
        return "idle"

    # A `waiting` that is only Claude's "you have not typed" nudge, recorded
    # by a hook from before that nudge was ignored on never-prompted sessions.
    # Left as-is it would hold `needs you` until the process died, since
    # nothing else clears it. Judged here so existing state files heal on
    # update without needing another event from that session.
    if reported == "waiting" and session.get("turn_over") is not False:
        detail = str(session.get("detail") or "").lower()
        if IDLE_NUDGE_TEXT in detail:
            return "idle"

    # `running` is the one state worth second-guessing, because it is the one
    # that gets left behind: a turn ended by an interrupt sends no `Stop`, and
    # the pet then sat on "working" for the whole five-minute backstop. Only
    # `running` -- `waiting` is a claim about the user, not about the process,
    # and an idle Claude is exactly what it looks like.
    if reported == "running" and age > IDLE_CPU_SECONDS:
        busy_at = session.get("busy_at")
        if isinstance(busy_at, (int, float)) and now - busy_at > IDLE_CPU_SECONDS:
            return "idle"

    return reported


def aggregate(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collapse every live session into the one state the pet should show.

    Time-dependent: the same file aggregates differently as dwells expire, so
    callers should re-run this on a timer rather than only on file changes.
    """
    data = read() if data is None else data
    now = time.time()
    live = [
        session
        for session in data.get("sessions", {}).values()
        if isinstance(session, dict) and is_alive(session, now)
    ]
    if not live:
        return {
            "state": "idle", "sessions": 0, "detail": "", "cwd": "",
            "locator": None, "since": now,
        }

    order = {name: index for index, name in enumerate(PRIORITY)}
    ranked = [(effective_state(session, now), session) for session in live]
    state_name, winner = min(
        ranked, key=lambda pair: (order.get(pair[0], len(PRIORITY)), -pair[1]["ts"])
    )
    identifiers = {id(session): key for key, session in data.get("sessions", {}).items()}
    return {
        "state": state_name,
        "sessions": len(live),
        # An expired session has nothing left to say, so drop its stale detail.
        "detail": str(winner.get("detail") or "") if state_name != "idle" else "",
        "cwd": str(winner.get("cwd") or ""),
        # Where to jump when the pet is clicked.
        "locator": winner.get("locator") if isinstance(winner.get("locator"), dict) else None,
        "since": winner.get("ts", now),
        # Which session is speaking, and what put it there. Only used for the
        # transition log, where "the pet says X" is useless without "because".
        # `stored` comes along so the log can tell an arriving event apart from
        # a dwell simply running out -- otherwise a state that expired reads as
        # though the last event caused it.
        "winner": {
            "id": identifiers.get(id(winner)),
            "event": winner.get("event"),
            "stored": winner.get("state"),
        },
    }
