"""Recording where a Claude session lives, so the pet can jump back to it.

Collected by the hook, which runs on every tool call, so this only reads
`/proc` and the environment -- no X queries, no subprocesses. Resolving those
pids to an actual window is the overlay's job, and only when clicked.

Stdlib only.
"""

from __future__ import annotations

import os
from typing import Any

from . import agents, desktop

#: Processes between the hook and the terminal that are never the terminal.
_TRANSPARENT = {
    "bash", "sh", "zsh", "fish", "dash", "python3", "python", "node",
    "claude", "claude-pet", "tmux", "tmux: server", "su", "sudo", "env",
}

_MAX_DEPTH = 16


def _parent_of(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def ancestor_pids() -> list[int]:
    """Pids from this process up to init, nearest first.

    The terminal emulator is somewhere in here. Which one it is cannot be known
    without asking the window server, so the whole chain is recorded and the
    overlay matches it against window ownership later.
    """
    chain: list[int] = []
    pid = os.getpid()
    for _ in range(_MAX_DEPTH):
        parent = _parent_of(pid)
        if parent is None or parent <= 1:
            break
        chain.append(parent)
        pid = parent
    return chain


def terminal_candidates() -> list[int]:
    """Ancestors that could plausibly own a terminal window."""
    return [pid for pid in ancestor_pids() if _comm(pid) not in _TRANSPARENT]


#: `comm` of the Claude Code process. Kept for callers that predate several
#: agents being supported; `agent_process()` is the general answer.
CLAUDE_COMM = "claude"


def agent_process() -> tuple[str, int] | None:
    """Nearest ancestor that is a coding agent, and which one it is.

    Recorded separately from the rest of the chain because it is the session's
    liveness signal, and the chain is useless for that -- it ends at systemd,
    which never exits. Which agent it is has to be settled here too: later on
    there is only a pid, and `gemini` and any other Node program look alike.
    """
    for pid in ancestor_pids():
        found = agents.identify(pid)
        if found is not None:
            return found, pid
    return None


def claude_pid() -> int | None:
    """Nearest ancestor that is the Claude Code process itself."""
    found = agent_process()
    return found[1] if found is not None and found[0] == "claude" else None


def locator() -> dict[str, Any]:
    """Everything known about where this session is running."""
    chain = ancestor_pids()[:_MAX_DEPTH]
    found: dict[str, Any] = {"pids": chain}

    owner = agent_process()
    if owner is not None:
        found["agent"], found["claude_pid"] = owner
        # `claude_pid` keeps its name: entries written by earlier versions use
        # it, and renaming it would strand every session already on disk.

    # Claude Desktop runs the same Claude Code binary, so its sessions arrive
    # here indistinguishably -- except in the ancestry. Recording which side a
    # session came from now is the only chance to know: the app is
    # Wayland-native, so afterwards it owns no window to recognise it by.
    found["origin"] = desktop.origin_of(chain)

    pane = os.environ.get("TMUX_PANE")
    tmux = os.environ.get("TMUX")
    if pane and tmux:
        # $TMUX is "socket,pid,session-index"; the socket matters for -S.
        found["tmux_pane"] = pane
        found["tmux_socket"] = tmux.split(",")[0]

    term = os.environ.get("TERM_PROGRAM")
    if term:
        found["term_program"] = term

    # Which terminal *widget* this session is in, where the terminal says so.
    #
    # A terminal that serves every window from one process gives them all the
    # same `_NET_WM_PID`, so raising "the window owned by that pid" raises
    # whichever comes first -- reported as the pet jumping to a different
    # window than the session that was waiting. Terminator puts a per-terminal
    # uuid in the environment and will map it to a window on request, which is
    # the only thing here that tells its windows apart.
    uuid = os.environ.get("TERMINATOR_UUID")
    if uuid:
        found["terminal_uuid"] = uuid
        bus = os.environ.get("TERMINATOR_DBUS_NAME")
        path = os.environ.get("TERMINATOR_DBUS_PATH")
        if bus and path:
            found["terminal_bus"] = bus
            found["terminal_path"] = path
    return found
