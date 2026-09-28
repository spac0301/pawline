"""Console entry point preserves the native app-server's stdin/stdout protocol."""
from fluff_monitor.capture import main
if __name__ == '__main__':
    raise SystemExit(main())
