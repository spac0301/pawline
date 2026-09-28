"""Making the user's terminal reachable, so clicking the pet can raise it.

A Wayland-native terminal cannot be raised by another application -- see
`jump.py`. Running the same terminal under XWayland puts it back within reach of
`wmctrl`, and the way to arrange that for *every* launch path is a wrapper on
PATH plus a desktop-file override:

- `Ctrl+Alt+T` runs `x-terminal-emulator`, resolved through PATH. Both
  gnome-shell and gsd-media-keys carry `~/.local/bin` at the front of theirs,
  so a wrapper there wins.
- The launcher and dock read the desktop file, which PATH does not affect.

Only applied when there is no better option: a terminal that answers
`org.freedesktop.Application` can raise itself and needs none of this.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

MARKER = "# added by claude-pet"

#: Names to shadow on PATH. `x-terminal-emulator` is what Ctrl+Alt+T runs.
SHADOW_NAMES = ("x-terminal-emulator",)


def local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def desktop_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "applications"


def default_terminal() -> tuple[str, Path] | None:
    """The terminal Ctrl+Alt+T would start, as (name, real path).

    Resolves *through* a wrapper we installed earlier, so a re-run can still
    repair the desktop file rather than reporting nothing to wrap.
    """
    launcher = shutil.which("x-terminal-emulator")
    if launcher is None:
        return None

    path = Path(launcher)
    if local_bin() in path.parents:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if MARKER not in body:
            return None  # somebody else's wrapper; leave it be
        for token in body.split():
            candidate = Path(token.strip('"'))
            if candidate.is_absolute() and candidate.exists():
                return candidate.name, candidate
        return None

    real = path.resolve()
    return real.name, real


def wrapper_path(name: str) -> Path:
    return local_bin() / name


def wrapper_installed() -> bool:
    """Whether every shadow wrapper is present and ours."""
    for name in SHADOW_NAMES:
        path = wrapper_path(name)
        try:
            if not path.is_file() or MARKER not in path.read_text(encoding="utf-8"):
                return False
        except OSError:
            return False
    return True


#: Ancestors that are never the terminal. gnome-shell in particular answers
#: D-Bus introspection happily, and it is in the chain of anything it launched,
#: so probing it made every terminal look like it could raise itself.
NOT_THE_TERMINAL = {"gnome-shell", "systemd", "gsd-media-keys", "init"}

APPLICATION_INTERFACE = "org.freedesktop.Application"


def _comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def raises_itself(pids: list[int]) -> bool:
    """Whether the terminal owning one of `pids` can present itself over D-Bus."""
    from . import jump

    if not shutil.which("gdbus"):
        return False

    wanted = {pid for pid in pids if _comm(pid) not in NOT_THE_TERMINAL}
    if not wanted:
        return False

    for name, owner in jump._bus_names():  # noqa: SLF001 - same package
        if owner not in wanted:
            continue
        path = "/" + name.replace(".", "/").replace("-", "_")
        probe = subprocess.run(  # noqa: S603 - fixed argv
            ["gdbus", "introspect", "--session", "--dest", name, "--object-path", path],
            capture_output=True, text=True, timeout=5, check=False,
        )
        # A zero exit only means the object exists; the interface has to be there.
        if probe.returncode == 0 and APPLICATION_INTERFACE in probe.stdout:
            return True
    return False


def needed() -> bool:
    """Whether wrapping the terminal would actually buy anything."""
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False  # on X11 the terminal is already reachable
    if not (shutil.which("wmctrl") or shutil.which("xdotool")):
        return False  # nothing to raise it with even under XWayland
    if default_terminal() is None:
        return False

    # A terminal that can present itself needs no help, and forcing it onto
    # XWayland would cost it crisp scaling for nothing.
    from . import locate

    return not raises_itself(locate.ancestor_pids())


def install(quiet: bool = False) -> bool:
    """Install the PATH wrappers and desktop override. Returns True on change."""
    found = default_terminal()
    if found is None:
        if not quiet:
            print("  no x-terminal-emulator on PATH; nothing to wrap")
        return False
    name, real = found

    body = (
        "#!/bin/sh\n"
        f"{MARKER}: run under XWayland so clicking the pet can raise this window.\n"
        "# Remove this file (or run `claude-pet fix-terminal --undo`) to revert.\n"
        f'exec env GDK_BACKEND=x11 "{real}" "$@"\n'
    )

    changed = False
    try:
        local_bin().mkdir(parents=True, exist_ok=True)
        for shadow in SHADOW_NAMES:
            path = wrapper_path(shadow)
            if path.exists() and MARKER not in path.read_text(encoding="utf-8"):
                if not quiet:
                    print(f"  {path} exists and is not ours; left alone")
                continue
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
            changed = True
            if not quiet:
                print(f"  wrapped {shadow} -> GDK_BACKEND=x11 {real}")
    except OSError as exc:
        print(f"claude-pet: could not write the wrapper: {exc}")
        return changed

    if _patch_desktop_file(name, quiet=quiet):
        changed = True
    if changed and not quiet:
        print("  close and reopen your terminal for this to take effect")
    return changed


def _patch_desktop_file(name: str, quiet: bool = False) -> bool:
    """Copy the terminal's desktop file locally and prefix its Exec lines."""
    source = next(
        (
            candidate
            for candidate in (
                Path(f"/usr/share/applications/{name}.desktop"),
                Path(f"/usr/local/share/applications/{name}.desktop"),
            )
            if candidate.is_file()
        ),
        None,
    )
    if source is None:
        return False

    target = desktop_dir() / source.name
    try:
        if target.is_file() and MARKER in target.read_text(encoding="utf-8"):
            return False
        lines = source.read_text(encoding="utf-8").splitlines()
        patched = [
            f"Exec=env GDK_BACKEND=x11 {line[len('Exec='):]}" if line.startswith("Exec=") else line
            for line in lines
        ]
        patched.insert(1, f"{MARKER}: prefixed Exec with GDK_BACKEND=x11")
        desktop_dir().mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(patched) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"claude-pet: could not write {target}: {exc}")
        return False

    if not quiet:
        print(f"  patched {target}")
    return True


def uninstall() -> bool:
    """Remove anything `install()` wrote. Returns True if something went."""
    removed = False
    for shadow in SHADOW_NAMES:
        path = wrapper_path(shadow)
        try:
            if path.is_file() and MARKER in path.read_text(encoding="utf-8"):
                path.unlink()
                removed = True
                print(f"  removed {path}")
        except OSError as exc:
            print(f"claude-pet: could not remove {path}: {exc}")

    directory = desktop_dir()
    if directory.is_dir():
        for candidate in directory.glob("*.desktop"):
            try:
                if MARKER in candidate.read_text(encoding="utf-8"):
                    candidate.unlink()
                    removed = True
                    print(f"  removed {candidate}")
            except OSError:
                continue
    return removed
