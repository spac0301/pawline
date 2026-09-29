"""Run the native CLI with bounded, asynchronous metadata observation.

Relay callbacks only enqueue. Parsing/aggregation and private snapshot IO run
on one worker, outside the socket pumps. No provider probes are generated.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys

from . import proxy, storage
from .observation import DesktopCapture  # compatibility for existing integrations
from .observer_host import ObserverHost, request_reload
from .observer_runtime import install_bundle
from .identity import set_process_name
from .platform_support import native_library_search_path


def resolve_real_cli(env):
    """A saved install target also works in the browser's filtered environment."""
    value = env.get("CODEX_ROUTING_REAL_CLI")
    if not value:
        saved = storage.read_json(storage.state_dir() / "capture-config.json")
        value = saved.get("real_cli") if saved.get("schema") == "fluff_capture_config_v1" else None
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise ValueError("Configure the native Codex executable with --fluff-configure-cli PATH or CODEX_ROUTING_REAL_CLI.")
    binary = Path(value).resolve()
    adapter = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "launch-capture.sh"
    if binary == adapter.resolve():
        raise ValueError("The native CLI target must not point back to this adapter.")
    return str(binary)


def configure_cli(argv):
    parser = argparse.ArgumentParser(description="Remember this installation's native Codex CLI; no account credentials are saved.")
    parser.add_argument("cli", type=Path)
    args = parser.parse_args(argv)
    try:
        binary = resolve_real_cli({"CODEX_ROUTING_REAL_CLI": str(args.cli.expanduser().absolute())})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    storage.atomic_json(storage.state_dir() / "capture-config.json",
                        {"schema": "fluff_capture_config_v1", "real_cli": binary})
    print("Native Codex CLI configured. Desktop observation requires FLUFF_DESKTOP_CAPTURE=1.")
    return 0


def run_native_cli(binary, args, env, *, replace_process=False):
    """Keep stdio and exit status attached to the caller on both platforms."""
    if replace_process and sys.platform != "win32":
        return os.execve(binary, [binary, *args], env)
    # Windows execve exits the wrapper before its child finishes. Popen owns
    # the child until completion and handles Windows argv quoting and pipes.
    with native_library_search_path():
        child = subprocess.Popen(
            [binary, *args], env=env, stdin=sys.stdin,
            stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
    previous = {}
    def forward(sig, _):
        if child.poll() is None:
            if sys.platform == "win32" and sig == signal.SIGINT:
                try:
                    child.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:  # A GUI launcher may not have a console.
                    child.terminate()
            else:
                child.send_signal(sig)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, forward)
        return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["--fluff-reload-observer"], ["--fluff-update-observer"]):
        try:
            update = args[0] == "--fluff-update-observer"
            if len(args) != (2 if update else 1):
                raise ValueError('Use --fluff-reload-observer or --fluff-update-observer PACKAGE.zip')
            if update:
                install_bundle(args[1], storage.state_dir())
            result = request_reload()
            print(f"Pawline observer {result['version']} reloaded; Codex and relay remain running.")
            return 0
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            print(str(error), file=sys.stderr)
            return 1
    if args[:1] == ["--fluff-configure-cli"]:
        return configure_cli(args[1:])
    env = dict(os.environ)
    try:
        binary = resolve_real_cli(env)
    except ValueError as exc:
        print("Routing adapter: " + str(exc), file=sys.stderr)
        return 1
    observe_desktop = env.pop("FLUFF_DESKTOP_CAPTURE", None) == "1"
    if not observe_desktop or "app-server" not in args:
        # Browser/config helpers must reach the real CLI and its policy checks
        # without starting another observer or replacing desktop.json.
        return run_native_cli(binary, args, env, replace_process=True)
    if any(env.get(key) for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")):
        print("Fluff capture requires a direct upstream connection. An existing proxy is configured; refusing to overwrite or bypass it.", file=sys.stderr)
        return 1
    ca = proxy.CertAuthority()
    set_process_name('pawline-relay')
    try:
        capture = ObserverHost()
    except (OSError, RuntimeError) as error:
        ca.close()
        print(f"Pawline observer unavailable: {error}", file=sys.stderr)
        return run_native_cli(binary, args, env)
    original_ca = env.get("CODEX_CA_CERTIFICATE")
    upstream_context = ssl.create_default_context(cafile=original_ca) if original_ca else None
    transport = proxy.InterceptProxy(ca, on_message=capture.feed, on_event=capture.event,
        upstream_context=upstream_context,
        intercept_hosts={os.environ.get("CODEX_ROUTING_CAPTURE_HOST", "chatgpt.com")}, require_auth=True)
    try:
        transport.start()
        add, drop = transport.env()
        for key in drop:
            env.pop(key, None)
        env.update(add)
        return run_native_cli(binary, args, env)
    finally:
        transport.stop()
        capture.close()
        ca.close()


if __name__ == "__main__":
    raise SystemExit(main())
