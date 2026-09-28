# Local adapter changes

The upstream revision is recorded in UPSTREAM.json and its MIT LICENSE is retained.
The only vendored source change is `config.config_dir()`: `CODEX_ROUTING_PET_CONFIG`
can select this adapter's own configuration directory. The normal default is preserved.

`fluff_monitor.pet` subclasses Overlay with `poll=False` and supplies its own
status panel, menu and lifecycle. `fluff_monitor.qt_pet` reuses only the sprite
loader. Neither entry point installs upstream hooks, starts update/usage checks
or desktop discovery, or takes over an existing claude-pet instance.
