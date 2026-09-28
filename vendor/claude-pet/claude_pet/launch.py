"""Starting the overlay as a detached process.

Shared by the `SessionStart` hook and `claude-pet run --detach` so there is one
place that knows how to launch it. Going through the `claude-pet` launcher
matters: that script is what pins `GDK_BACKEND=x11`, and without it the overlay
comes up on the Wayland backend where mutter ignores always-on-top.

Stdlib only -- the hook path imports this on every session start.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import state


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launcher_path() -> Path:
    return project_root() / "claude-pet"


def overlay_pid() -> int | None:
    """PID of the running overlay, or None if the pidfile is stale or absent.

    Checks that the pid is *ours*, not merely alive. A pidfile left behind by
    a crash names a number the kernel hands out again -- to a terminal, to
    Claude itself, to anything -- and `stop` and the updater both SIGTERM
    whatever this returns. Existence alone was one recycled pid away from
    killing the wrong program.
    """
    try:
        pid = int(state.pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    cmdline = _cmdline_of(pid)
    if cmdline is None:
        return None
    return pid if "claude_pet" in cmdline or "claude-pet" in cmdline else None


def _cmdline_of(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace"
        )
    except OSError:
        return None


def spawn_detached(reason: str = "") -> int | None:
    """Start the overlay in its own session so it outlives the terminal.

    Returns the child pid, or None if it was already running or could not be
    started. Never raises: a pet that fails to appear must not break anything.
    """
    if overlay_pid() is not None:
        return None

    root = project_root()
    try:
        log_dir = state.state_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / "overlay.log", "ab", buffering=0)  # noqa: SIM115 - child owns it
    except OSError:
        return None

    environment = {**os.environ}
    if reason:
        environment["CLAUDE_PET_LAUNCHED_BY"] = reason
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{root}:{existing}" if existing else str(root)

    launcher = launcher_path()
    if os.access(launcher, os.X_OK):
        argv = [str(launcher), "run"]
    else:
        argv = [sys.executable, "-m", "claude_pet", "run"]
        environment.setdefault("GDK_BACKEND", "x11")

    try:
        child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,  # survives the terminal that started it
            cwd=str(root),
            env=environment,
        )
    except OSError:
        return None
    finally:
        handle.close()
    return child.pid
