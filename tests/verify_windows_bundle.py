"""Exercise the packaged executables with isolated state and synthetic artwork."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this check on Windows after windows/build.ps1.")
    dist = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    pet = dist / "pawline/pawline.exe"
    capture = dist / "pawline-capture/pawline-capture.exe"
    for executable in (pet, capture):
        if not executable.is_file():
            raise RuntimeError(f"Missing executable: {executable}")
        for notice in ("Python-LICENSE.txt", "Qt-6.11.2-NOTICES.txt", "Qt-SOURCES.json",
                       "runtime-packages.json"):
            if not (executable.parent / "licenses" / notice).is_file():
                raise RuntimeError(f"Missing runtime notice: {notice}")
    unexpected = [str(path.relative_to(dist)) for path in dist.rglob("*")
                  if path.is_file() and any(part in path.name.lower()
                                           for part in ("virtualkeyboard", "qt6pdf", "qpdf."))]
    if unexpected:
        raise RuntimeError(f"Unused Qt Addons entered the bundle: {unexpected}")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        pack = root / "pet"
        pack.mkdir()
        atlas = Image.new("RGBA", (1536, 1872))
        draw = ImageDraw.Draw(atlas)
        for row in range(9):
            draw.rectangle((20, row * 208 + 20, 172, row * 208 + 195),
                           fill=(150, 160, 180, 255))
        atlas.save(pack / "spritesheet.png")
        (pack / "pet.json").write_text(json.dumps({
            "id": "bundle-fixture", "name": "Bundle fixture",
            "spritesheetPath": "spritesheet.png",
        }), encoding="utf-8")
        env = dict(os.environ, FLUFF_STATE_DIR=str(root / "state"),
                   CODEX_HOME=str(root / "codex"), QT_QPA_PLATFORM="offscreen")
        env.pop("CODEX_ROUTING_REAL_CLI", None)
        env.pop("FLUFF_DESKTOP_CAPTURE", None)
        configured = subprocess.run(
            [str(capture), "--fluff-configure-cli", sys.executable],
            env=env, capture_output=True, text=True, timeout=30)
        if configured.returncode:
            raise RuntimeError(f"Capture setup failed: {configured.stderr}")
        native = subprocess.run([str(capture), "--version"], env=env,
                                capture_output=True, text=True, timeout=30)
        if native.returncode or "Python " not in native.stdout:
            raise RuntimeError(f"Native handoff failed: exit={native.returncode}, stdout={native.stdout!r}, stderr={native.stderr!r}")
        protocol = subprocess.run(
            [str(capture), "-c", "import sys; print(sys.stdin.read()); print('native-stderr', file=sys.stderr); sys.exit(23)"],
            env=env, input="native-stdin", capture_output=True, text=True, timeout=30)
        if protocol.returncode != 23 or protocol.stdout.strip() != "native-stdin" or protocol.stderr.strip() != "native-stderr":
            raise RuntimeError(f"Native stdio/exit handoff failed: {protocol!r}")
        launched = subprocess.run(
            [str(pet), "--pet-dir", str(pack), "--no-collector", "--test-seconds", "2"],
            env=env, capture_output=True, text=True, timeout=30)
        if launched.returncode:
            raise RuntimeError(f"Packaged Qt launch failed: {launched.stderr}")
        if (root / "state/desktop.json").exists():
            raise RuntimeError("A helper invocation started an observer.")
        result = {
            "passed": True, "platform": sys.platform, "model_calls": 0,
            "native_cli_handoff": True, "native_stdio_and_exit_status": True,
            "packaged_qt_start_and_exit": True,
            "runtime_notices_included": True, "unused_qt_addons_absent": True,
            "synthetic_sprite_only": True, "interactive_desktop_verified": False,
            "executables": {
                str(path.relative_to(dist)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (pet, capture)
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
