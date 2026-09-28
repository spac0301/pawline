"""Retain runtime notices and exact package versions in each portable bundle."""
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    distributions = [metadata.distribution(name) for name in (
        "PySide6_Essentials", "shiboken6", "Pillow", "cryptography", "cffi",
        "pycparser", "zstandard", "typing_extensions", "psutil", "pywin32",
        "pyinstaller",
    )]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The Windows Python runtime license is missing.")
    for app in ("pawline", "pawline-capture"):
        target = root / "dist" / app / "licenses"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, target / "Python-LICENSE.txt")
        for path in (root / "assets/licenses").iterdir():
            if path.is_file():
                shutil.copy2(path, target / path.name)
        inventory = []
        for dist in distributions:
            destination = target / dist.metadata["Name"]
            destination.mkdir(exist_ok=True)
            copied = []
            for file in dist.files or ():
                if (file.name.lower().startswith(("license", "copying", "notice"))
                        or "licenses" in file.parts):
                    source = Path(dist.locate_file(file))
                    if source.is_file():
                        # Preserve subdirectories when a wheel has many licenses.
                        relative = Path(*file.parts)
                        if ".." in relative.parts or relative.is_absolute():
                            raise RuntimeError(f"Unsafe license path in {dist.metadata['Name']}")
                        out = destination / relative
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, out)
                        copied.append(str(out.relative_to(target)))
            inventory.append({"name": dist.metadata["Name"], "version": dist.version,
                              "notices": copied})
        (target / "runtime-packages.json").write_text(
            json.dumps({"python": sys.version.split()[0], "packages": inventory}, indent=2)
            + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
