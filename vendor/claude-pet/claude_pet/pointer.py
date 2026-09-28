"""Whether X11 can still see where the pointer is.

The overlay runs under XWayland on purpose -- a Wayland-native window cannot
be put at a chosen place, kept above everything, and refused focus, which is
the whole of what an overlay is. The price is paid here: XWayland is told
where the pointer is only while the pointer is over one of *its* windows.
Move onto a Wayland-native window and the position stops updating, frozen at
wherever it was when it left.

Measured on a GNOME 46 Wayland session, driving the real pointer through the
compositor rather than XTest (an XTest warp goes through XWayland, so
XWayland necessarily knows about it, which makes it useless for this):

      pointer moved to      X11 reports
      3167 -> 2367          keeps up exactly
      onto the next screen  frozen at 1967
      back again            keeps up again

That screen was covered by a Wayland-native browser, and the one it kept up
on by a terminal running under XWayland. So a summons drawn over the
terminal was recognised and the same circle drawn over the browser did
nothing whatever, which is not a thing anyone would guess.

Nothing inside the process can fix it. GNOME 46 has no portal for reading the
pointer (GlobalShortcuts arrived later), `org.gnome.Shell.Eval` is locked,
and the remaining route -- a screencast session for its cursor metadata --
means a permission dialog and a screen-sharing indicator for the lifetime of
a desktop pet, which is not a trade worth making.

So there are two answers here, and the pet uses both.

Knowing. X11 says plainly whether it is still being told: asked who is under
the pointer it names a window while it is keeping up, and answers nobody once
it is not. That alone stops stale coordinates being read as a gesture, or
walked to as though the pointer were still there.

Asking someone who knows. The compositor never lost track -- the difficulty
was only that nothing would say. A GNOME Shell extension of one method
(`shellext.py`) exports `global.get_pointer()`, and when it is installed the
pet is callable from anywhere on screen. Only consulted when X11 has lost
sight, so the shell does no work in the ordinary case.
"""

from __future__ import annotations

import ctypes
import os
import time

#: Cached so the poll does not reopen a display connection every 50ms, and so
#: that a machine without libX11 pays for the failure once.
_xlib: ctypes.CDLL | None = None
_display: int | None = None
_root: int | None = None
_broken = False


def under_wayland() -> bool:
    """Whether the pointer can go somewhere X11 cannot follow.

    Only on Wayland. On a real X11 session nothing is invisible, and the
    same "nobody is under the pointer" answer means only that it is over the
    desktop background -- where the position is perfectly good.
    """
    return os.environ.get("XDG_SESSION_TYPE", "") == "wayland"


def _connect() -> bool:
    global _xlib, _display, _root, _broken
    if _broken:
        return False
    if _display is not None:
        return True
    try:
        _xlib = ctypes.CDLL("libX11.so.6")
        _xlib.XOpenDisplay.restype = ctypes.c_void_p
        _xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        _xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        _xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        _xlib.XQueryPointer.argtypes = (
            [ctypes.c_void_p, ctypes.c_ulong]
            + [ctypes.POINTER(ctypes.c_ulong)] * 2
            + [ctypes.POINTER(ctypes.c_int)] * 4
            + [ctypes.POINTER(ctypes.c_uint)]
        )
        display = _xlib.XOpenDisplay(None)
        if not display:
            raise OSError("no display")
        _display = display
        _root = _xlib.XDefaultRootWindow(display)
    except (OSError, AttributeError):
        # No libX11, or no display to open. Never ask again: this is on the
        # gesture poll, and a failing lookup every 50ms is worse than not
        # knowing.
        _broken = True
        return False
    return True


def _query_child() -> int | None:
    """Which X11 window is under the pointer; None if X11 cannot be asked.

    Kept apart from the decision so the decision can be tested: the answer
    comes out of libX11 and there is no arranging it from a test.
    """
    if not _connect():
        return None
    assert _xlib is not None and _display is not None and _root is not None
    root_ret, child = ctypes.c_ulong(), ctypes.c_ulong()
    root_x, root_y, win_x, win_y = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    try:
        _xlib.XQueryPointer(
            _display, _root,
            ctypes.byref(root_ret), ctypes.byref(child),
            ctypes.byref(root_x), ctypes.byref(root_y),
            ctypes.byref(win_x), ctypes.byref(win_y), ctypes.byref(mask),
        )
    except OSError:
        return None
    return child.value


def visible() -> bool:
    """Whether the pointer's position can be trusted right now.

    True when X11 names a window under the pointer, and on anything that is
    not a Wayland session. Errs towards True: a summons that is occasionally
    missed is a smaller fault than a pet that stops answering because this
    went wrong.
    """
    if not under_wayland():
        return True
    child = _query_child()
    return True if child is None else child != 0


#: Set once the shell bridge has been asked and found missing, so a poll that
#: runs while the pointer is somewhere X11 cannot see does not make a D-Bus
#: round trip every 50ms to be told the same thing.
_bridge: object | None = None
_bridge_missing_at: float | None = None

#: ...but not forever. A single timeout while the shell was busy would
#: otherwise cost the bridge for the rest of the session, and a pet that
#: quietly stops answering is the fault this whole module exists to avoid.
BRIDGE_RETRY_SECONDS = 30.0

#: Long enough that the 60Hz walk loop reuses one answer for a few frames,
#: short enough that the 50ms gesture poll always gets a fresh one.
#:
#: Without this the walk loop asked the shell sixty times a second, each a
#: synchronous round trip into the compositor's own main loop. Reported as a
#: circle going unrecognised over a Wayland window and a pet stopping partway
#: through an errand: samples arrived late enough to break the gesture up, and
#: one slow reply was enough to strand the walk.
SAMPLE_SECONDS = 0.04

#: Short. This runs on a timer with sixteen milliseconds to spare, and a
#: reply that has not come by now is one to do without rather than wait for.
BRIDGE_TIMEOUT_MS = 60

BRIDGE_PATH = "/org/gnome/Shell/Extensions/ClaudePetPointer"
BRIDGE_IFACE = "org.gnome.Shell.Extensions.ClaudePetPointer"


def from_shell() -> tuple[int, int] | None:
    """Ask the compositor directly, through the extension, if it is there.

    The compositor always knows where the pointer is; the difficulty was
    only ever that nothing would say. Asked for rather than watched, so the
    shell does no work unless the pet needs an answer -- and it only needs
    one when X11 has lost sight, which is why this is not the first thing
    tried.
    """
    global _bridge, _bridge_missing_at
    if _bridge_missing_at is not None:
        if time.monotonic() - _bridge_missing_at < BRIDGE_RETRY_SECONDS:
            return None
        _bridge_missing_at = None
    try:
        from gi.repository import Gio

        if _bridge is None:
            _bridge = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None, "org.gnome.Shell", BRIDGE_PATH, BRIDGE_IFACE, None,
            )
        reply = _bridge.call_sync(
            "GetPointer", None, Gio.DBusCallFlags.NONE, BRIDGE_TIMEOUT_MS, None
        )
    except Exception as exc:  # noqa: BLE001 -- absent, refusing, or no gi at all
        # A timeout is the shell being busy, not the bridge being gone.
        # Backing off thirty seconds for one slow reply is what stranded a
        # walk halfway: the pet carried on to wherever it was last aiming and
        # stopped there, which looks exactly like it changed its mind.
        if not _is_transient(exc):
            _bridge_missing_at = time.monotonic()
            _bridge = None
        return None
    try:
        x, y = reply.unpack()
    except (ValueError, TypeError):
        _bridge_missing_at = time.monotonic()
        return None
    return int(x), int(y)


def _is_transient(exc: Exception) -> bool:
    """Whether this failure is worth trying again straight away.

    A timeout or a dropped reply is the shell being busy. Anything else --
    no such name, no such object, no such method -- is the extension not
    being installed, which is worth backing off from.
    """
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return False
    if not isinstance(exc, GLib.Error):
        return False
    quark = Gio.DBusError.quark()
    return any(
        exc.matches(quark, code)
        for code in (Gio.DBusError.TIMEOUT, Gio.DBusError.NO_REPLY, Gio.DBusError.TIMED_OUT)
    )


# --- A non-blocking bridge read, for the hot loop -----------------------------
#
# `from_shell` above is synchronous: it asks the shell and waits up to
# BRIDGE_TIMEOUT_MS for the answer. That is fine for doctor, which asks once,
# and wrong for the 16ms walk loop and 50ms gesture poll, which ask constantly.
# The bridge runs *inside* gnome-shell's own main loop -- the very loop that is
# compositing whatever heavy web page is on screen -- so under load the reply
# does not come within a frame, the sync call times out and returns None, the
# walk target stops being updated, and the pointer's samples are dropped. That
# is the "call fails, and the pet stops as if at a wall, but only on a busy
# page" report exactly.
#
# So the hot loop uses this instead: it fires the request and does not wait,
# reading whatever answer last came back. A busy compositor makes that answer a
# little older, never absent, and never blocks the pet's own loop.

#: The freshest bridge answer that has arrived, and when.
_async_pos: tuple[int, int] | None = None
_async_at = 0.0
#: One request outstanding at a time; no point piling them up on a slow shell.
_async_inflight = False

#: How old the last answer may be and still be used. Longer than a handful of
#: frames, so an ordinary compositor hitch is ridden out on the last position
#: rather than reported as "nobody can say"; short enough that a genuinely lost
#: pointer is not walked toward for long.
ASYNC_STALE_SECONDS = 0.30


def _on_async_reply(proxy, result, _user_data) -> None:
    global _async_pos, _async_at, _async_inflight, _bridge, _bridge_missing_at
    _async_inflight = False
    try:
        reply = proxy.call_finish(result)
        x, y = reply.unpack()
    except Exception as exc:  # noqa: BLE001
        # A timeout is the shell being busy; anything else is it being gone.
        if not _is_transient(exc):
            _bridge_missing_at = time.monotonic()
            _bridge = None
        return
    _async_pos, _async_at = (int(x), int(y)), time.monotonic()


def _pump_async() -> None:
    """Fire one bridge request if none is in flight, and never wait for it."""
    global _bridge, _bridge_missing_at, _async_inflight
    if _async_inflight:
        return
    if _bridge_missing_at is not None:
        if time.monotonic() - _bridge_missing_at < BRIDGE_RETRY_SECONDS:
            return
        _bridge_missing_at = None
    try:
        from gi.repository import Gio

        if _bridge is None:
            _bridge = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None, "org.gnome.Shell", BRIDGE_PATH, BRIDGE_IFACE, None,
            )
        _async_inflight = True
        _bridge.call(
            "GetPointer", None, Gio.DBusCallFlags.NONE,
            BRIDGE_TIMEOUT_MS, None, _on_async_reply, None,
        )
    except Exception as exc:  # noqa: BLE001
        _async_inflight = False
        if not _is_transient(exc):
            _bridge_missing_at = time.monotonic()
            _bridge = None


def from_shell_live() -> tuple[int, int] | None:
    """The freshest bridge answer without ever blocking; None if none is fresh.

    Delivered by the GLib main loop the overlay is already running, so the
    request fired here is answered between the pet's own ticks. Outside a main
    loop -- doctor, a test -- no answer is ever delivered and this returns None;
    those call `from_shell` directly, which does wait.
    """
    _pump_async()
    if _async_pos is not None and time.monotonic() - _async_at < ASYNC_STALE_SECONDS:
        return _async_pos
    return None


def bridge_present() -> bool:
    """Whether the shell answers, checked afresh rather than from the cache."""
    global _bridge, _bridge_missing_at, _async_pos, _async_at, _async_inflight
    _bridge, _bridge_missing_at = None, None
    _async_pos, _async_at, _async_inflight = None, 0.0, False
    forget()
    return from_shell() is not None


def forget() -> None:
    """Drop the cached reading, so the next ask goes and looks."""
    global _sample, _sample_at
    _sample, _sample_at = None, 0.0


#: The last answer, and when it was taken.
_sample: tuple[int, int] | None = None
_sample_at = 0.0


def trusted_position(raw) -> tuple[int, int] | None:
    """Where the pointer is, or None when nobody can say.

    X11 first, because when it is keeping up it is free and exact, and
    asking the shell twenty times a second for something already to hand is
    work the compositor should not be doing. The bridge is for the case X11
    cannot cover, which is the only case it was installed for.
    """
    return sample(raw)[0]


def sample(raw) -> tuple[tuple[int, int] | None, bool]:
    """The pointer, and whether this is a fresh reading or a reused one.

    Two callers want this at once -- the gesture poll every 50ms and the walk
    loop sixty times a second -- and on the bridge path each reading is a
    round trip into the compositor. So one reading serves both, and the walk
    loop is told when it is looking at a repeat: `settled` is judged from
    consecutive readings, and a reused one would say the pointer had stopped
    when it had not.
    """
    global _sample, _sample_at
    now = time.monotonic()
    if _sample is not None and now - _sample_at < SAMPLE_SECONDS:
        return _sample, False
    fresh = raw() if visible() else from_shell_live()
    if fresh is None:
        # Say nothing rather than repeat a stale answer: a position nobody can
        # vouch for is the thing this module exists to refuse.
        _sample, _sample_at = None, now
        return None, False
    _sample, _sample_at = fresh, now
    return fresh, True


def explain() -> str | None:
    """One line for `doctor`, or None when there is nothing to warn about."""
    if not under_wayland():
        return None
    if bridge_present():
        return None
    if _query_child() is None:
        return "cannot ask X11 where the pointer is; calling may not work"
    where = "over a window it can see" if visible() else "over a Wayland window, invisible to it"
    return (
        f"the pointer is {where}. Without the shell bridge the pet can only be "
        f"called from over an X11/XWayland window; run `claude-pet fix-pointer` "
        f"to install it, then log out and back in."
    )
