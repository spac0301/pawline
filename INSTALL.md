# Linux source installation

For the Windows source and recipient checks, see WINDOWS.md. This section
describes the exercised Linux installation. It was exercised
with CPython 3.10 and GTK3/X11. Do not copy another machine's virtualenv,
credentials, Codex/Claude profile, CA directory or captured state.

1. Read `README.md` and `SECURITY.md`. Install Python 3.10+, venv,
   PyGObject/GTK3 and cairo through your distribution's trusted package source
   if they are not already present. Those OS packages are not bundled.
2. Extract the `pawline` source directory to `~/.local/share/fluff-monitor`. Back up any existing
   installation before replacing it. From that directory create a private
   runtime:

   ```sh
   chmod u+x launch-pet.sh launch-capture.sh
   python3 -m venv --system-site-packages .venv
   .venv/bin/python -m pip install -r requirements.lock
   .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -v
   ```

   The dependency versions are pinned, but the archive does not bundle binary
   wheels or replace OS security updates. Review PyPI downloads and package
   updates according to your environment's policy.
3. Register the local, passive collector in your own user service manager:

   ```sh
   systemctl --user link "$HOME/.local/share/fluff-monitor/fluff-monitor-activity.service"
   systemctl --user enable --now fluff-monitor-activity.service
   ```

4. Start `./launch-pet.sh --pet-dir /path/to/a/trusted/sprite-pack`. Personal
   sprite assets are intentionally not included. The path must be a compatible
   local sprite directory, not an arbitrary executable or downloaded installer.

## Optional actual-response observation

Usage collection and TLS interception are separate capabilities. Read the
security section before enabling interception. This integration was prepared
for the local Codex desktop's CLI adapter mechanism; it is not a promise of a
stable, public desktop API or compatibility with every future app release.

Register **this machine's bundled Codex executable** once:

```sh
./launch-capture.sh --fluff-configure-cli /path/to/the/apps/bundled/codex
```

This stores only its absolute executable path in the private local
`capture-config.json`. Browser/config helpers can resolve the native CLI even
when their environment does not include `CODEX_ROUTING_REAL_CLI`.

The desktop app's launch environment needs `FLUFF_DESKTOP_CAPTURE=1` and
`CODEX_CLI_PATH` set to this installation's `launch-capture.sh`.
`CODEX_ROUTING_REAL_CLI` can explicitly override the saved target. Do not copy
another user's absolute executable path or capture configuration.
The capture opt-in is consumed before starting the native CLI: helper processes
delegate to the real CLI without creating a proxy or replacing the live snapshot.
No global proxy or system CA setting should be installed. Existing proxy
configuration is refused instead of bypassed. Keep the original app launcher
available so interception can be disabled by removing this adapter setting.

Activate changes by a normal app restart after active work is safe to interrupt.
Check that the shared `desktop.json` identifies `fluff-monitor/0.2.4`, has a fresh
timestamp, and shows metadata from the intended task's normal response. A
running process or a local mock test alone is not that check. No artificial
provider request or cache-warming prompt is needed.
