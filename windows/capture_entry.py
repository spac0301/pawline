"""Console entry point preserves the native app-server's stdin/stdout protocol."""
if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--fluff-observer-worker':
        from fluff_monitor.observer_runtime import bootstrap
        raise SystemExit(bootstrap(sys.argv[2]))
    from fluff_monitor.capture import main
    raise SystemExit(main())
