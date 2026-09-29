"""Register this Linux installation as Pawline in application/process lists."""
from pathlib import Path
import os
import shutil
import subprocess


def install(root=None):
    root = Path(root or __file__).resolve()
    if root.is_file():
        root = root.parent
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    icons = data / "icons/hicolor/512x512/apps"
    applications = data / "applications"
    for directory in (icons, applications):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "assets/app/pawline.png", icons / "pawline.png")
    executable = str(root / "launch-pet.sh").replace("%", "%%")
    for character in ("\\", '"', '`', '$'):
        executable = executable.replace(character, "\\" + character)
    entry = ("[Desktop Entry]\nType=Application\nName=Pawline\n"
             "Comment=Codex와 Claude Code의 작업 상태 보기\n"
             f'Exec="{executable}"\nIcon=pawline\nTerminal=false\n'
             "Categories=Development;Utility;\nStartupNotify=false\nStartupWMClass=Pawline\n")
    target = applications / "pawline.desktop"
    target.write_text(entry, encoding="utf-8")
    legacy = applications / "codex-routing-pet.desktop"
    if legacy.is_file() and "StartupWMClass=Codex-routing-pet" in legacy.read_text():
        # Preserve an existing pinned shortcut without a duplicate menu entry.
        legacy.write_text(entry + 'NoDisplay=true\n', encoding='utf-8')
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(applications)], check=True)
    icon_updater = shutil.which('gtk-update-icon-cache')
    if icon_updater:
        subprocess.run([icon_updater, '-f', '-t', str(data / 'icons/hicolor')], check=True)
    return target


if __name__ == "__main__":
    print(install())
