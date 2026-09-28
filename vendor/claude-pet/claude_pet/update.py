"""Updating an existing install in place.

Two install shapes exist and they update differently: a git clone pulls, and a
tarball install from `install.sh` re-downloads. Both end up at the same place,
so `claude-pet update` picks the right one rather than making the user know
which they have.

Stdlib only.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "HaneulOscarLee/claude-pet"
BRANCH = "main"
TIMEOUT_SECONDS = 30

#: Written next to the code by a tarball update, since there is no git history
#: to ask.
VERSION_FILE = ".claude-pet-version"


class UpdateError(Exception):
    """Raised when the update cannot proceed."""


def install_root() -> Path:
    from . import launch

    return launch.project_root()


def is_git_checkout(root: Path | None = None) -> bool:
    return ((root or install_root()) / ".git").exists()


def releases_url() -> str:
    return f"https://github.com/{REPO}/releases/latest"


def is_system_install(root: Path | None = None) -> bool:
    """A package-managed install: owned by root, so we must not rewrite it."""
    return not os.access(root or install_root(), os.W_OK)


def _git(*args: str, root: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(root or install_root()), *args],
        capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
    )


def installed_version() -> str:
    root = install_root()
    if is_git_checkout(root):
        result = _git("rev-parse", "--short", "HEAD", root=root)
        return result.stdout.strip() or "unknown"
    marker = root / VERSION_FILE
    try:
        return marker.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def latest_release_info() -> tuple[str, str | None]:
    """Newest release: its version, and the .deb to install if there is one."""
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    request = urllib.request.Request(
        url, headers={"User-Agent": "claude-pet", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise UpdateError(f"could not reach GitHub: {exc}") from exc

    tag = payload.get("tag_name")
    if not tag:
        raise UpdateError("GitHub returned no release")

    package = None
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".deb") and asset.get("browser_download_url"):
            package = asset["browser_download_url"]
            break
    return tag.lstrip("v"), package


def latest_release() -> str:
    """Tag of the newest release, without the leading v.

    What a packaged install should compare against: a new .deb only exists when
    a release is cut, so comparing with the tip of the branch would report an
    update every time main moved.
    """
    return latest_release_info()[0]


def latest_sha() -> str:
    """The current head of the branch, from the GitHub API."""
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "claude-pet", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise UpdateError(f"could not reach GitHub: {exc}") from exc
    sha = payload.get("sha")
    if not sha:
        raise UpdateError("GitHub returned no commit for the branch")
    return sha


def _update_git(root: Path) -> tuple[str, str]:
    dirty = _git("status", "--porcelain", root=root).stdout.strip()
    if dirty:
        raise UpdateError(
            "this checkout has uncommitted changes; commit or stash them first\n"
            + "\n".join(f"  {line}" for line in dirty.splitlines()[:10])
        )

    before = _git("rev-parse", "--short", "HEAD", root=root).stdout.strip()
    fetch = _git("fetch", "--quiet", "origin", BRANCH, root=root)
    if fetch.returncode:
        raise UpdateError(f"git fetch failed: {fetch.stderr.strip()}")

    merge = _git("merge", "--ff-only", f"origin/{BRANCH}", root=root)
    if merge.returncode:
        raise UpdateError(
            f"cannot fast-forward: {merge.stderr.strip()}\n"
            "  the local branch has diverged; resolve it with git"
        )
    after = _git("rev-parse", "--short", "HEAD", root=root).stdout.strip()
    return before, after


def _update_tarball(root: Path) -> tuple[str, str]:
    before = installed_version()
    url = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
    request = urllib.request.Request(url, headers={"User-Agent": "claude-pet"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            archive = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"download failed: {exc}") from exc

    with tempfile.TemporaryDirectory() as workspace:
        try:
            zipfile.ZipFile(io.BytesIO(archive)).extractall(workspace)
        except (zipfile.BadZipFile, OSError) as exc:
            raise UpdateError(f"the download was not a usable archive: {exc}") from exc

        extracted = next((path for path in Path(workspace).iterdir() if path.is_dir()), None)
        if extracted is None or not (extracted / "claude-pet").exists():
            raise UpdateError("the archive did not contain a claude-pet install")

        # Replace tracked content only; pets and config live outside the install.
        for item in extracted.iterdir():
            target = root / item.name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            shutil.move(str(item), str(target))

    (root / "claude-pet").chmod(0o755)
    after = latest_sha()[:7]
    (root / VERSION_FILE).write_text(after + "\n", encoding="utf-8")
    return before, after


def check() -> dict[str, str | bool]:
    """Installed vs latest, without touching anything.

    Raises UpdateError if GitHub cannot be reached, so callers can tell "no
    update" apart from "could not look".

    A git checkout that is *ahead* of the branch -- local work not pushed yet --
    is not out of date, though its sha differs from the branch head. Comparing
    the two for mere inequality lit up "an update is available" for exactly the
    state that most needs it not to: your own unpushed commits, which a
    fast-forward then refuses to touch. So a checkout already containing the
    branch head is up to date.
    """
    current = installed_version()
    if is_system_install():
        latest = latest_release()
        return {"current": current, "latest": latest, "available": current != latest}

    latest = latest_sha()
    available = latest[:7] != current and not _contains_commit(latest)
    return {"current": current, "latest": latest[:7], "available": available}


def _contains_commit(sha: str) -> bool:
    """Whether this checkout's history already includes `sha`.

    True when the branch head is an ancestor of HEAD -- i.e. we are level with
    it or ahead of it. `merge-base --is-ancestor` is exactly this question and
    exits 0 for yes; anything else (not an ancestor, unknown sha, not a repo)
    is a no.
    """
    try:
        return _git("merge-base", "--is-ancestor", sha, "HEAD").returncode == 0
    except (OSError, UpdateError):
        return False


def update(check_only: bool = False) -> int:
    root = install_root()
    current = installed_version()
    if is_system_install(root):
        kind = "system package"
    elif is_git_checkout(root):
        kind = "git checkout"
    else:
        kind = "tarball install"
    print(f"install : {root}  ({kind})")
    print(f"version : {current}")

    # A package-managed install belongs to the package manager. Rewriting /usr
    # behind its back would leave dpkg's idea of the files wrong -- but a
    # command called `update` that only prints a link has not updated anything,
    # so it hands the new package to the installer instead.
    if is_system_install(root) and not check_only:
        return _update_package(current)

    if check_only:
        try:
            latest = latest_release() if is_system_install(root) else latest_sha()[:7]
        except UpdateError as exc:
            print(f"claude-pet: {exc}")
            return 1
        print(f"latest  : {latest}")
        print("up to date" if latest == current else "an update is available: claude-pet update")
        return 0

    try:
        before, after = _update_git(root) if is_git_checkout(root) else _update_tarball(root)
    except UpdateError as exc:
        print(f"claude-pet: {exc}")
        return 1

    if before == after:
        print("already up to date")
        return 0

    print(f"updated : {before} -> {after}")
    if is_git_checkout(root):
        log = _git("log", "--oneline", f"{before}..{after}", root=root).stdout.strip()
        for line in log.splitlines()[:10]:
            print(f"  {line}")

    _report_new_requirements()
    _ensure_pointer_bridge()
    _restart_overlay()
    return 0


def apply() -> str:
    """Do the update but restart nothing; return what happened.

    For the overlay, which is itself the thing `update()` would restart --
    calling that in-process would have it kill its own thread. This does the
    fetch-and-fast-forward (or tarball swap) and reports one of:

        "updated"  the files changed; the caller should respawn
        "current"  nothing to do -- already level, or a checkout ahead of the
                   branch, which is not a failure and must not read as one
        "failed"   could not, e.g. a checkout with unpushed commits that will
                   not fast-forward

    A packaged install is not handled here; that path stays detached because
    the system installer outlives the pet.
    """
    root = install_root()
    if is_system_install(root):
        return "failed"
    try:
        before, after = _update_git(root) if is_git_checkout(root) else _update_tarball(root)
    except UpdateError as exc:
        print(f"claude-pet: {exc}")
        return "failed"
    if before == after:
        return "current"
    print(f"updated : {before} -> {after}")
    _report_new_requirements()
    _ensure_pointer_bridge()
    return "updated"


#: Optional pieces that a version might newly depend on, and how to get them.
#: Package names per distro, because "install the app indicator bindings" is
#: not a thing anyone can act on.
_OPTIONAL = (
    (
        "status-bar menu",
        lambda: __import__("claude_pet.tray", fromlist=["tray"]).available(),
        {
            "apt": "gir1.2-ayatanaappindicator3-0.1",
            "dnf": "libayatana-appindicator-gtk3",
            "pacman": "libayatana-appindicator",
            "zypper": "typelib-1_0-AyatanaAppIndicator3-0_1",
        },
    ),
)


def _package_manager() -> tuple[str, str] | None:
    for manager, install in (
        ("apt", "sudo apt install"),
        ("dnf", "sudo dnf install"),
        ("pacman", "sudo pacman -S"),
        ("zypper", "sudo zypper install"),
    ):
        if shutil.which(manager):
            return manager, install
    return None


def _report_new_requirements() -> None:
    """Name anything the new version can use but cannot find.

    An update replaces code, not packages. Without this a feature added in a
    release is simply absent for everyone who updates rather than installs,
    with nothing on screen to say why -- which is the worst way to ship one.
    """
    missing = []
    for label, probe, packages in _OPTIONAL:
        try:
            if probe():
                continue
        except Exception:  # noqa: BLE001 - a probe must not break the update
            continue
        missing.append((label, packages))
    if not missing:
        return

    found = _package_manager()
    print("\nthis version can do more with a package you do not have:")
    for label, packages in missing:
        if found is not None and found[0] in packages:
            print(f"  {label}: {found[1]} {packages[found[0]]}")
        else:
            print(f"  {label}: install one of {', '.join(sorted(set(packages.values())))}")


def _update_package(current: str) -> int:
    """Install the newest release over a package-managed install.

    dpkg has to do the writing, so this downloads the .deb and asks the system
    installer to take it, prompting through polkit the way any desktop
    application updating a system package does. Printing a link and stopping
    was honest about the constraint and useless about the goal: people ran
    `update`, watched nothing happen, and reported the update as broken.
    """
    try:
        latest, package_url = latest_release_info()
    except UpdateError as exc:
        print(f"claude-pet: {exc}")
        return 1

    if latest == current:
        print("already up to date")
        return 0
    print(f"latest  : {latest}")

    installer = _graphical_installer()
    if package_url is None or installer is None:
        # Nothing to hand over, or nothing to hand it to.
        print("this is a system package; install the new .deb from:")
        print(f"  {releases_url()}")
        return 0

    cache = Path(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    ) / "claude-pet"
    target = cache / package_url.rsplit("/", 1)[-1]
    print(f"downloading {target.name}...")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(package_url, headers={"User-Agent": "claude-pet"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read()
        target.write_bytes(payload)
    except (urllib.error.URLError, OSError) as exc:
        print(f"claude-pet: download failed: {exc}")
        print(f"  get it by hand from {releases_url()}")
        return 1

    print("installing (you will be asked to authenticate)...")
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            installer + [str(target)], timeout=300, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"claude-pet: could not run the installer: {exc}")
        print(f"  the package is at {target}")
        return 1

    if done.returncode != 0:
        # Cancelled at the password prompt, most likely.
        print("claude-pet: the install did not complete")
        print(f"  the package is at {target}, or get it from {releases_url()}")
        return 1

    print(f"updated : {current} -> {latest}")
    _report_new_requirements()
    _ensure_pointer_bridge()
    _restart_overlay()
    return 0


def _graphical_installer() -> list[str] | None:
    """Argv that installs a local .deb, asking for authority as it goes."""
    if shutil.which("pkexec") and shutil.which("apt-get"):
        # polkit puts up the desktop's own password prompt. `env -i` is not
        # used: apt needs a sane environment, and pkexec already scrubs it.
        return ["pkexec", "apt-get", "install", "-y", "--allow-downgrades"]
    if shutil.which("gdebi-gtk"):
        return ["gdebi-gtk"]
    return None


def _ensure_pointer_bridge() -> None:
    """Lay down the Wayland pointer bridge on update, as setup does.

    Idempotent and GNOME/Wayland-only; installing does not activate it, so the
    overlay reminds the user to log out and back in on its next start -- which
    is the only channel a menu-driven update has.
    """
    try:
        from . import shellext

        if shellext.ensure() == "installed":
            print("installed the pointer bridge; log out and back in to finish it")
    except Exception:  # noqa: BLE001 -- never let this fail an update
        pass


def _restart_overlay() -> None:
    """Put the running pet on the new code; leave it alone if it was not up."""
    from . import launch

    pid = launch.overlay_pid()
    if pid is None:
        return
    print("restarting the pet...")
    try:
        os.kill(pid, 15)
    except OSError:
        return
    for _ in range(30):
        if launch.overlay_pid() is None:
            break
        import time

        time.sleep(0.1)
    if launch.spawn_detached(reason="update") is None:
        print("claude-pet: could not restart; run `claude-pet run --detach`")
