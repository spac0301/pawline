"""PyInstaller entry point; platform libraries stay outside the shared core."""
from fluff_monitor.qt_pet import main
if __name__ == '__main__':
    raise SystemExit(main())
