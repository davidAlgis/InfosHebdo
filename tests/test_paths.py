"""Tests des chemins : l'executable que le lancement au demarrage doit inscrire."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from infoshebdo import paths, startup


class TestFrozenCommandPrefix(unittest.TestCase):
    def test_packaged_application_runs_the_executable_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "InfosHebdo.exe"
            exe.write_bytes(b"")
            with mock.patch.object(paths, "FROZEN", True), \
                 mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(paths.command_prefix(), [str(exe.resolve())])

    def test_startup_entry_launches_the_ui_minimized(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "InfosHebdo.exe"
            exe.write_bytes(b"")
            with mock.patch.object(paths, "FROZEN", True), \
                 mock.patch.object(sys, "executable", str(exe)):
                command = startup.startup_command()
        self.assertTrue(command.startswith('"'))
        self.assertTrue(command.endswith("InfosHebdo.exe\" ui --minimized"))

    def test_spaced_install_path_is_quoted_in_the_registry_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Mes Programmes"
            folder.mkdir()
            exe = folder / "InfosHebdo.exe"
            exe.write_bytes(b"")
            with mock.patch.object(paths, "FROZEN", True), \
                 mock.patch.object(sys, "executable", str(exe)):
                command = startup.startup_command()
        self.assertIn('"', command.split(" ui --minimized")[0])
        self.assertIn("Mes Programmes", command)


class TestSourceCommandPrefix(unittest.TestCase):
    def test_source_mode_runs_the_launcher_with_utf8(self):
        with mock.patch.object(paths, "FROZEN", False):
            prefix = paths.command_prefix()
        self.assertEqual(prefix[-1], str(paths.LAUNCHER))
        self.assertIn("-X", prefix)

    def test_launcher_is_absolute(self):
        # Le lancement a la session part d'un repertoire quelconque.
        self.assertTrue(paths.LAUNCHER.is_absolute())


if __name__ == "__main__":
    unittest.main()
