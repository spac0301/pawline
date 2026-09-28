"""Jumping from the pet back to the Claude session that wants attention.

What is actually possible depends on the setup, so this tries in order of
reliability and reports honestly when none of it applies:

1. **tmux** -- switch the client to the recorded pane. Works everywhere,
   compositor included, and is the only precise option: it lands on the exact
   pane rather than merely raising a window.
2. **X11 / XWayland terminals** -- activate the window owned by one of the
   session's ancestor processes, via `wmctrl` or `xdotool`.
3. **D-Bus** -- ask the terminal to present *itself*, via
   `org.freedesktop.Application.Activate`. A client may not raise someone
   else's window under mutter, but an application is always allowed to raise
   its own, so this is the only route that can work for a Wayland-native
   terminal -- and only for terminals that implement it (GApplication-based
   ones do; Terminator does not).

What is *not* possible is reaching over and raising an unco-operative Wayland
window: `org.gnome.Shell.FocusApp`, `.Introspect` and `.Eval` all answer
AccessDenied, and xdg-activation needs a token only the target app can hand out.
Running such a terminal under XWayland puts it back within reach of route 2.

Every route must only report success when the window actually moves. Returning
success for a call that did nothing is worse than admitting defeat, because the
pet then says it raised a terminal that is still buried.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from . import desktop

TIMEOUT_SECONDS = 3


class JumpResult:
    """Outcome of a jump attempt, with something to show the user."""

    def __init__(self, ok: bool, message: str) -> None:
        self.ok = ok
        self.message = message

    def __bool__(self) -> bool:
        return self.ok


def _run(argv: list[str]) -> bool:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _desktop_jump(locator: dict[str, Any]) -> JumpResult | None:
    """Sessions running inside Claude Desktop go back to the app, not a terminal.

    Tried first because none of the other routes could ever work for one: the
    app is Wayland-native so it owns no X window, it exports no session-bus
    name, and a session it started has no tmux pane. Without this they would
    all decline in turn and the pet would say it had nowhere to go.
    """
    if locator.get("origin") != desktop.ORIGIN_DESKTOP:
        return None
    ok, message = desktop.focus()
    return JumpResult(ok, message)


def _tmux_jump(locator: dict[str, Any]) -> JumpResult | None:
    pane = locator.get("tmux_pane")
    socket = locator.get("tmux_socket")
    if not pane or not shutil.which("tmux"):
        return None

    base = ["tmux"]
    if socket:
        base += ["-S", str(socket)]

    # select-window first so the pane's window becomes current, then the pane.
    ok = _run(base + ["select-window", "-t", str(pane)])
    ok = _run(base + ["select-pane", "-t", str(pane)]) or ok

    # And only then, if nobody is already looking at that session, hand exactly
    # one client to it.
    #
    # `switch-client -t <pane>` with no `-c` was the bug: run from outside tmux
    # there is no current client, so tmux picks one itself -- and it picks a
    # client watching something else entirely. Measured on tmux 3.4: a client
    # on session `beta` was moved to `alpha` by a jump aimed at a pane in
    # `alpha`. Someone with a terminal per session saw their windows dragged
    # one by one onto whichever session the pet had just pointed at, which is
    # the "all my sessions turned into the same session" report.
    if not _tmux_session_shown(base, pane):
        client = _tmux_spare_client(base, pane)
        if client:
            _run(base + ["switch-client", "-c", client, "-t", str(pane)])
    if ok:
        return JumpResult(True, f"switched to {pane}")
    return JumpResult(False, f"tmux could not select {pane}")


def _tmux_query(base: list[str], argv: list[str]) -> str:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv
            base + argv, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _tmux_session_shown(base: list[str], pane: str) -> bool:
    """Whether some client is already attached to the pane's own session.

    If one is, selecting the window was enough -- that client is now showing
    the pane, and moving anybody else would only take a window away from
    whoever was using it.
    """
    return bool(_tmux_query(base, ["list-clients", "-t", str(pane), "-F", "#{client_name}"]).split())


def _tmux_spare_client(base: list[str], pane: str) -> str | None:
    """A client that can be lent to the pane's session, or None to leave well alone.

    Only a client whose own session has no *other* client is offered: moving it
    inconveniences nobody else, since it is the only pair of eyes there. When
    every client is the sole viewer of its session there is nothing safe to
    move, and the jump settles for having selected the window -- better than
    yanking a session out from under another window.
    """
    session_of: dict[str, str] = {}
    for line in _tmux_query(
        base, ["list-clients", "-F", "#{client_name}\t#{client_session}"]
    ).splitlines():
        name, _, session = line.partition("\t")
        if name and session:
            session_of[name] = session
    if not session_of:
        return None
    target = _tmux_query(
        base, ["display-message", "-p", "-t", str(pane), "#{session_name}"]
    ).strip()
    counts: dict[str, int] = {}
    for session in session_of.values():
        counts[session] = counts.get(session, 0) + 1
    # Prefer a client that is one of several on its session: taking it away
    # leaves that session still visible to somebody.
    for name, session in session_of.items():
        if session != target and counts.get(session, 0) > 1:
            return name
    return None


def _tmux_client_pids(locator: dict[str, Any]) -> list[int]:
    """Pids of whatever is currently displaying this tmux pane.

    A session inside tmux has no route to its terminal through its own
    ancestry. The shell's parent is the tmux *server*, which is detached from
    every terminal and owns no window -- so the recorded chain reads
    `bash -> tmux: server -> systemd` and matches nothing on screen. The
    terminal is the parent of the tmux *client*, and which client that is can
    change at any time (detach here, attach there), so it is asked at the
    moment of the jump rather than recorded alongside the pane.
    """
    pane = locator.get("tmux_pane")
    if not pane or not shutil.which("tmux"):
        return []

    argv = ["tmux"]
    socket = locator.get("tmux_socket")
    if socket:
        argv += ["-S", str(socket)]
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv
            argv + ["list-clients", "-t", str(pane), "-F", "#{client_pid}"],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    from . import locate

    found: list[int] = []
    for token in listing.split():
        if not token.isdigit():
            continue
        pid = int(token)
        for _ in range(8):
            found.append(pid)
            parent = locate._parent_of(pid)
            if parent is None or parent <= 1:
                break
            pid = parent
    return found


def _terminal_window_title(locator: dict[str, Any]) -> str | None:
    """The title of the window holding this session's terminal, if it will say.

    Terminator serves every window from one process, so `_NET_WM_PID` cannot
    tell them apart -- but it hands each terminal a uuid through the
    environment and will map that uuid to a window title on request. The title
    is then something `wmctrl` can match, which is how the right window of
    several gets picked.

    Returns None whenever anything is missing, which is most terminals.
    """
    uuid = locator.get("terminal_uuid")
    bus_name = locator.get("terminal_bus")
    path = locator.get("terminal_path")
    if not (isinstance(uuid, str) and isinstance(bus_name, str) and isinstance(path, str)):
        return None
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            bus_name, path, bus_name, "get_window_title",
            GLib.Variant("(s)", (uuid,)), None,
            Gio.DBusCallFlags.NONE, int(TIMEOUT_SECONDS * 1000), None,
        )
        title = reply.unpack()[0]
    except Exception:  # noqa: BLE001 -- gone, renamed, or not that terminal
        return None
    return title if isinstance(title, str) and title else None


def _shell_raise(locator: dict[str, Any]) -> "JumpResult | None":
    """Ask our GNOME Shell extension to raise the window, if it is there.

    The compositor may raise any window; an external client may not raise
    another application's. So this is the one route that works for a
    native Wayland terminal without wrapping it under XWayland -- which is
    what `fix-terminal` did, and what froze the terminal when an X11 window
    came and went. Tried first on that account: if it works, nothing external
    has to touch window stacking at all.
    """
    pids = [pid for pid in locator.get("pids") or [] if isinstance(pid, int)]
    pids += _tmux_client_pids(locator)
    if not pids:
        return None
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.gnome.Shell",
            "/org/gnome/Shell/Extensions/ClaudePetPointer",
            "org.gnome.Shell.Extensions.ClaudePetPointer",
            "RaiseWindowForPids",
            GLib.Variant("(ai)", (pids,)),
            None, Gio.DBusCallFlags.NONE, int(TIMEOUT_SECONDS * 1000), None,
        )
        raised = bool(reply.unpack()[0])
    except Exception:  # noqa: BLE001 -- extension absent, old, or not GNOME
        return None
    if raised:
        return JumpResult(True, "raised the terminal")
    # The extension answered but found no window for these pids -- let the
    # other routes try rather than reporting a definite failure.
    return None


def _x11_window_for(pids: list[int], title: str | None = None) -> str | None:
    """Window id owned by one of `pids`, using whatever tool is installed.

    With a `title`, the window has to match that as well, which is what makes
    the choice exact for a terminal serving several windows from one process:
    they all carry the same `_NET_WM_PID`, so without it the first listed wins
    and the pet jumps to a sibling of the window that was waiting.

    Without one it is the first match: exact for a terminal that runs a
    process per window, a guess for one that does not.
    """
    if shutil.which("wmctrl"):
        try:
            listing = subprocess.run(  # noqa: S603
                ["wmctrl", "-lp"], capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        owned = []
        for line in listing.splitlines():
            fields = line.split(None, 4)
            if len(fields) >= 3 and fields[2].isdigit() and int(fields[2]) in pids:
                owned.append((fields[0], fields[4] if len(fields) >= 5 else ""))
        if title:
            for window, name in owned:
                if name == title:
                    return window
        if owned:
            return owned[0][0]
    if shutil.which("xdotool"):
        for pid in pids:
            try:
                found = subprocess.run(  # noqa: S603
                    ["xdotool", "search", "--pid", str(pid)], capture_output=True,
                    text=True, timeout=TIMEOUT_SECONDS, check=False,
                ).stdout.split()
            except (OSError, subprocess.SubprocessError):
                continue
            if found:
                return found[-1]
    return None


def _x11_jump(locator: dict[str, Any]) -> JumpResult | None:
    pids = [pid for pid in locator.get("pids") or [] if isinstance(pid, int)]
    # For a tmux session these are the only pids that lead anywhere near a
    # window, and the recorded ones never do.
    pids += _tmux_client_pids(locator)
    if not pids:
        return None
    if not (shutil.which("wmctrl") or shutil.which("xdotool")):
        return None

    window = _x11_window_for(pids, _terminal_window_title(locator))
    if window is None:
        return None
    if shutil.which("wmctrl") and _run(["wmctrl", "-i", "-a", window]):
        return JumpResult(True, "raised the terminal")
    if shutil.which("xdotool") and _run(["xdotool", "windowactivate", window]):
        return JumpResult(True, "raised the terminal")
    return JumpResult(False, "could not raise the terminal")


def _bus_names() -> list[tuple[str, int]]:
    """Session-bus names with the pid that owns each, best effort."""
    if not shutil.which("busctl"):
        return []
    try:
        listing = subprocess.run(  # noqa: S603
            ["busctl", "--user", "list", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    owners: list[tuple[str, int]] = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            owners.append((fields[0], int(fields[1])))
    return owners


def _dbus_jump(locator: dict[str, Any]) -> JumpResult | None:
    """Ask the terminal to present itself, over D-Bus.

    The one approach that works for a Wayland-native terminal: a client may not
    raise someone else's window, but an application is always allowed to raise
    its own, and several terminals expose exactly that on the session bus.
    """
    pids = {pid for pid in locator.get("pids") or [] if isinstance(pid, int)}
    if not pids or not shutil.which("gdbus"):
        return None

    for name, owner in _bus_names():
        if owner not in pids:
            continue

        # Terminator is deliberately not special-cased. Its only candidate
        # method, unhide_cmdline, skips windows that are already visible:
        #
        #     for window in self.terminator.get_windows():
        #         if not window.get_property('visible'):
        #             window.on_hide_window()
        #
        # so it returns success and does nothing for a window that is merely
        # behind another one -- which made the pet claim it had raised a
        # terminal that never moved. Terminator under XWayland is handled by
        # the wmctrl route above instead.

        # The standard route for anything GTK/GApplication-based.
        path = "/" + name.replace(".", "/").replace("-", "_")
        if _run([
            "gdbus", "call", "--session", "--dest", name,
            "--object-path", path,
            "--method", "org.freedesktop.Application.Activate", "{}",
        ]):
            return JumpResult(True, "raised the terminal")
    return None


def capabilities() -> dict[str, bool]:
    """What jump methods this machine could use at all."""
    return {
        "tmux": shutil.which("tmux") is not None,
        "x11": bool(shutil.which("wmctrl") or shutil.which("xdotool")),
        "dbus": bool(shutil.which("gdbus") and shutil.which("busctl")),
        "desktop": desktop.installed(),
    }


def to_session(locator: dict[str, Any] | None) -> JumpResult:
    """Best-effort jump to the session described by `locator`."""
    if not locator:
        return JumpResult(False, "no location recorded for that session")

    # Exclusive: a session in the Claude Desktop app has no terminal window to
    # raise and no pane to select, so none of the rest can apply.
    result = _desktop_jump(locator)
    if result is not None:
        return result

    # Raising the window and selecting the pane are separate jobs, and tmux
    # inside a terminal needs both. tmux moves its own pane without touching
    # window stacking, so on its own it would land you on the right pane of a
    # window that is still behind everything else -- and say it had taken you
    # there. The window goes first so the pane switch happens in view.
    raised = None
    for attempt in (_shell_raise, _x11_jump, _dbus_jump):
        raised = attempt(locator)
        if raised is not None:
            break

    pane = _tmux_jump(locator)
    if pane is not None:
        if pane.ok and raised is not None and not raised.ok:
            return JumpResult(True, f"{pane.message}, but the window stayed put")
        return pane
    if raised is not None:
        return raised

    if locator.get("tmux_pane"):
        return JumpResult(False, "tmux is not installed")
    return JumpResult(False, "no way to raise this terminal — see the README")
