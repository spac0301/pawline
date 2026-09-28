"""Browser/config helpers must reach the native CLI without becoming observers."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fluff_monitor import capture


class CliHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.environ = patch.dict(os.environ, {"FLUFF_STATE_DIR": str(self.root)}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def configure(self):
        self.assertEqual(capture.main(["--fluff-configure-cli", sys.executable]), 0)

    def test_explicit_state_directory_works_without_a_home_profile(self):
        with patch.object(Path, "home", side_effect=RuntimeError("No home profile")):
            self.configure()
        self.assertTrue((self.root / "capture-config.json").is_file())

    def test_environment_filtered_helper_uses_saved_target_and_preserves_snapshot(self):
        self.configure()
        snapshot = self.root / "desktop.json"
        snapshot.write_bytes(b'{"owner":"desktop"}\n')
        with patch.object(capture.os, "execve") as execute, patch.object(capture, "DesktopCapture") as observer, patch.object(capture.proxy, "CertAuthority") as ca:
            capture.main(["app-server"])
        native = str(Path(sys.executable).resolve())
        execute.assert_called_once_with(native, [native, "app-server"], dict(os.environ))
        observer.assert_not_called()
        ca.assert_not_called()
        self.assertEqual(snapshot.read_bytes(), b'{"owner":"desktop"}\n')

    def test_helper_preserves_existing_environment_instead_of_replacing_proxy(self):
        self.configure()
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://example.invalid:3128"}), patch.object(capture.os, "execve") as execute:
            capture.main(["app-server"])
        self.assertEqual(execute.call_args.args[2]["HTTPS_PROXY"], "http://example.invalid:3128")

    def test_capture_opt_in_is_not_inherited_by_native_cli_tools(self):
        self.configure()
        with patch.dict(os.environ, {"FLUFF_DESKTOP_CAPTURE": "1"}), patch.object(capture.os, "execve") as execute:
            capture.main(["--version"])
        self.assertNotIn("FLUFF_DESKTOP_CAPTURE", execute.call_args.args[2])

    def test_explicit_invalid_target_does_not_silently_fall_back(self):
        self.configure()
        with patch.dict(os.environ, {"CODEX_ROUTING_REAL_CLI": str(self.root / "missing")}), patch.object(capture.os, "execve") as execute:
            self.assertEqual(capture.main(["--version"]), 1)
        execute.assert_not_called()

    def test_missing_configuration_fails_without_creating_an_observer(self):
        with patch.object(capture.os, "execve") as execute, patch.object(capture, "DesktopCapture") as observer:
            self.assertEqual(capture.main(["app-server"]), 1)
        execute.assert_not_called()
        observer.assert_not_called()
        self.assertFalse((self.root / "desktop.json").exists())

    def test_configuration_contains_only_the_validated_target_and_rejects_recursion(self):
        self.configure()
        saved = json.loads((self.root / "capture-config.json").read_text())
        self.assertEqual(saved, {"schema": "fluff_capture_config_v1", "real_cli": str(Path(sys.executable).resolve())})
        adapter = Path(capture.__file__).resolve().parents[1] / "launch-capture.sh"
        self.assertEqual(capture.main(["--fluff-configure-cli", str(adapter)]), 1)
        self.assertEqual(json.loads((self.root / "capture-config.json").read_text()), saved)
