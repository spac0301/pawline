"""Claude Desktop (the Electron app) as a second source of pet state.

Claude Code sessions started *inside* Claude Desktop already drive the pet with
no help from this module: the app downloads and runs the real Claude Code
binary (`~/.config/Claude/claude-code/<version>/claude`), which reads the same
`~/.claude/settings.json` and so fires the same hooks. Confirmed by watching a
session appear in the state file while that binary ran and vanish on SessionEnd.

Two things the app does not give us, which is what this module is for:

* **No hooks for a plain chat.** The web layer asks the shell to post a desktop
  notification when a reply lands, and that is the only turn-completion signal
  the app emits. `NotifyWatcher` eavesdrops on it. It only fires while the
  window is unfocused -- which is exactly when a pet is worth glancing at.
* **No window anyone else can raise.** The app is Wayland-native
  (`--ozone-platform=wayland`), so it owns no X window and neither wmctrl nor
  xdotool can see it. Its desktop entry sets `SingleMainWindow=true` and its
  second instance focuses the existing main window, so re-running the launcher
  is the app's own supported way of coming forward.

Import cost matters: this is reached from the hook, which runs inside every
Claude turn, so the module body stays stdlib-only. `NotifyWatcher` imports `gi`
lazily and is only ever constructed by the overlay.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any, Callable

#: `comm` of the Electron processes. The kernel truncates `comm` at 15
#: characters; this one fits with room to spare.
DESKTOP_COMM = "claude-desktop"

#: Launcher binary and desktop entry, both routes to focusing a running app.
LAUNCHER = "claude-desktop"
DESKTOP_ENTRY = "com.anthropic.Claude.desktop"

#: Stored in a session locator so the overlay knows a click has to go to the
#: app rather than hunt for a terminal window that does not exist.
ORIGIN_DESKTOP = "desktop"
ORIGIN_TERMINAL = "terminal"

#: Session id the app's plain-chat state is filed under. Not a real Claude
#: session id -- there is only ever one app -- and deliberately readable, since
#: it shows up in `claude-pet status`.
SESSION_ID = "claude-desktop"

TIMEOUT_SECONDS = 3

#: `main_pid()` sweeps /proc, and the overlay asks on every poll.
_SCAN_TTL_SECONDS = 3.0
_scan_cache: tuple[float, int | None] | None = None


def _comm_of(pid: str | int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def _cmdline_of(pid: str | int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            raw = stream.read()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def main_pid(*, cached: bool = True) -> int | None:
    """Pid of the Electron *main* process, or None if the app is not running.

    Chromium forks a zygote, a gpu process, a network service and a renderer per
    window, and every one of them shares the same `comm`. Only the main process
    is worth recording: it is the one whose death means the app is gone, and it
    is the only one launched without a `--type=` role.
    """
    global _scan_cache
    now = time.monotonic()
    if cached and _scan_cache is not None and now - _scan_cache[0] < _SCAN_TTL_SECONDS:
        return _scan_cache[1]

    found: int | None = None
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit() or _comm_of(entry.name) != DESKTOP_COMM:
                continue
            if any(arg.startswith("--type=") for arg in _cmdline_of(entry.name)):
                continue  # a Chromium helper, not the app itself
            found = int(entry.name)
            break
    except OSError:
        found = None

    _scan_cache = (now, found)
    return found


def running() -> bool:
    return main_pid() is not None


def installed() -> bool:
    return shutil.which(LAUNCHER) is not None or _entry_path() is not None


def origin_of(pids: list[int]) -> str:
    """Whether a session's ancestry runs through Claude Desktop.

    A Claude Code session the app started has the Electron process among its
    ancestors; one started in a terminal does not. That single fact decides
    where a click on the pet should land, so it is recorded at hook time
    rather than guessed later from a window list the app never appears in.
    """
    for pid in pids:
        if _comm_of(pid) == DESKTOP_COMM:
            return ORIGIN_DESKTOP
    return ORIGIN_TERMINAL


def _entry_path() -> str | None:
    directories = [
        os.path.expanduser("~/.local/share/applications"),
        *(
            os.path.join(base, "applications")
            for base in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(
                ":"
            )
            if base
        ),
    ]
    for directory in directories:
        candidate = os.path.join(directory, DESKTOP_ENTRY)
        if os.path.isfile(candidate):
            return candidate
    return None


def _spawn(argv: list[str]) -> bool:
    """Start `argv` detached and report whether it got off the ground.

    Deliberately not waited on: the launcher hands off to the running instance
    and then lingers, so a `run()` here would block until the app quit.
    """
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def focus() -> tuple[bool, str]:
    """Bring the Claude Desktop window forward.

    Nothing here raises someone else's window, which mutter would refuse.
    Running the launcher again reaches the *existing* instance, and an
    application is always allowed to raise itself -- the same trick the D-Bus
    route in `jump` uses for GApplication terminals, in the only form this app
    offers. What the app then does with the window is the app's own handler,
    which does cover a minimised one:

        mainWindow.isVisible() || mainWindow.show(),
        mainWindow.isMinimized() && mainWindow.restore(),
        mainWindow.focus()

    Reported not to come back from minimised, which is not yet explained. Note
    that no activation token can be supplied from here: a compositor only
    mints one against the serial of a real input event, and this overlay runs
    on XWayland, whose events carry no Wayland serial.
    """
    if main_pid() is None:
        return False, "Claude Desktop is not running"

    if shutil.which(LAUNCHER) and _spawn([LAUNCHER]):
        return True, "raised Claude Desktop"

    entry = _entry_path()
    if entry and shutil.which("gio") and _spawn(["gio", "launch", entry]):
        return True, "raised Claude Desktop"

    return False, "could not reach Claude Desktop"


def locator() -> dict[str, Any]:
    """Locator for the app itself, for the synthetic chat session."""
    found: dict[str, Any] = {"origin": ORIGIN_DESKTOP, "comm": DESKTOP_COMM}
    pid = main_pid()
    if pid is not None:
        found["claude_pid"] = pid
    return found


# --------------------------------------------------------------- notifications

#: How long a notification-derived state stays up, per state.
#:
#: A Claude Code session reports a *state* and keeps reporting it, so `waiting`
#: is allowed to hold indefinitely -- a pet that stops asking for you defeats
#: the point. A notification is an *edge*: the app says a reply arrived, and
#: nothing will ever say it was read. Left on the shared dwell table a
#: notification-derived `waiting` would latch on forever, so these override it.
NOTIFY_DWELL_SECONDS: dict[str, float] = {
    "review": 20.0,   # same as a finished turn anywhere else
    "waiting": 60.0,  # longer, because it is the more urgent of the two
}

def classify(summary: str, body: str) -> str:
    """Pet state for a notification the app just posted: always `done`.

    This used to read the text for words like "permission" or "승인" and
    promote those to `needs you`. It was wrong in both directions and wrong in
    the worse one more often: `확인` -- "check", "confirm", the label on half
    the OK buttons in Korean -- turned ordinary "your reply is ready" notices
    into a pet demanding attention nobody had asked for, for a full minute at a
    time, on the highest-priority state there is.

    Guessing was also unnecessary. Where `needs you` genuinely applies -- the
    app asking to run something -- that work goes through Claude Code, which
    reports `Notification` through the hooks and states it outright. Guessing
    from notification prose only ever added noise on top of a signal that was
    already accurate.

    So a notification means what it reliably means: something finished.
    """
    return "review"


def is_claude_notification(app_name: str, hints: dict[str, Any] | None) -> bool:
    """Whether a Notify call came from Claude Desktop.

    Matched on both the advertised app name and the `desktop-entry` hint,
    because Electron sets one or the other depending on version. Our own
    notifications are excluded explicitly -- the pet reacting to itself would
    latch `review` on forever.
    """
    entry = ""
    if isinstance(hints, dict):
        entry = str(hints.get("desktop-entry") or "")
    haystack = f"{app_name} {entry}".lower()
    if "claude-pet" in haystack or "claude_pet" in haystack:
        return False
    return "claude" in haystack or "anthropic" in haystack


class NotifyWatcher:
    """Reports Claude Desktop's desktop notifications as pet state.

    A plain chat has no hook surface, and this is the only signal the app emits
    that means "the turn is over". Seeing it needs a *monitor* connection:
    `Notify` is a method call addressed to the notification daemon, not a
    broadcast, so an ordinary match rule never delivers it. `BecomeMonitor`
    also makes a connection useless for anything else, which is why this opens
    a private one instead of borrowing the shared session bus.

    Failure is not an error. Any desktop without an eavesdroppable bus simply
    gets no chat state, and everything else about the pet carries on.
    """

    #: A notification is not always one message. On GNOME the well-known name
    #: is held by a relay that forwards the call on to the real daemon, so a
    #: monitor sees both hops and would otherwise report every reply twice.
    #: Identical text arriving inside this window is treated as the same event.
    ECHO_SECONDS = 2.0

    def __init__(self, on_notification: Callable[[str, str], None]) -> None:
        self._on_notification = on_notification
        self._connection: Any = None
        self._last: tuple[str, str, str] | None = None
        self._last_at = 0.0
        self.error: str | None = None

    def _is_echo(self, app_name: str, summary: str, body: str) -> bool:
        now = time.monotonic()
        signature = (app_name, summary, body)
        if self._last == signature and now - self._last_at < self.ECHO_SECONDS:
            return True
        self._last = signature
        self._last_at = now
        return False

    @property
    def active(self) -> bool:
        return self._connection is not None

    def start(self) -> bool:
        try:
            from gi.repository import Gio, GLib
        except ImportError as exc:  # pragma: no cover - GI is a hard dep of the overlay
            self.error = str(exc)
            return False

        rule = (
            "type='method_call',interface='org.freedesktop.Notifications',member='Notify'"
        )
        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            connection = Gio.DBusConnection.new_for_address_sync(
                address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.Monitoring",
                "BecomeMonitor",
                GLib.Variant("(asu)", ([rule], 0)),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - any failure means "no chat state"
            self.error = str(exc)
            return False

        connection.add_filter(self._filter)
        self._connection = connection
        return True

    def stop(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.close_sync(None)
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass

    def _filter(self, _connection: Any, message: Any, _incoming: bool) -> Any:
        """Runs on GLib's worker thread, so it only hands work to the main loop."""
        try:
            if message.get_member() != "Notify":
                return None
            body = message.get_body()
            fields = body.unpack() if body is not None else ()
            if len(fields) < 7:
                return None
            app_name, _replaces, _icon, summary, text, _actions, hints = fields[:7]
            if not is_claude_notification(str(app_name), hints):
                return None
            if self._is_echo(str(app_name), str(summary), str(text)):
                return None
        except Exception:  # noqa: BLE001 - a malformed message must not kill the filter
            return None

        state_name = classify(str(summary), str(text))
        detail = str(summary or text or "").strip()
        try:
            from gi.repository import GLib

            GLib.idle_add(self._deliver, state_name, detail)
        except Exception:  # noqa: BLE001
            pass
        # A monitor is an observer: consume the copy, never forward it.
        return None

    def _deliver(self, state_name: str, detail: str) -> bool:
        try:
            self._on_notification(state_name, detail)
        except Exception:  # noqa: BLE001 - a bad callback must not stop the watcher
            pass
        return False  # GLib.idle_add: run once
