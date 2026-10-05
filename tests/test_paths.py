"""Tests des chemins : surtout l'executable que la planification doit inscrire.

Empaquete, il existe deux executables : l'un sans console, l'autre avec. La
tache planifiee et le lancement a la session doivent toujours viser le
premier, quel que soit celui avec lequel on les a installes : sinon une
console noire s'ouvre a chaque declenchement.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from infoshebdo import paths


class TestFrozenCommandPrefix(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        (self.root / "InfosHebdo.exe").write_bytes(b"")
        (self.root / "InfosHebdo-console.exe").write_bytes(b"")

    def _prefix(self, executable: str) -> list[str]:
        with mock.patch.object(paths, "FROZEN", True), \
             mock.patch.object(sys, "executable", str(self.root / executable)):
            return paths.command_prefix()

    def test_windowless_exe_is_used_when_launched_from_it(self):
        self.assertEqual(self._prefix("InfosHebdo.exe"), [str(self.root / "InfosHebdo.exe")])

    def test_windowless_exe_is_preferred_when_launched_from_the_console_one(self):
        self.assertEqual(
            self._prefix("InfosHebdo-console.exe"), [str(self.root / "InfosHebdo.exe")]
        )

    def test_falls_back_to_the_running_exe_if_the_sibling_is_missing(self):
        (self.root / "InfosHebdo.exe").unlink()
        console = str((self.root / "InfosHebdo-console.exe").resolve())
        self.assertEqual(self._prefix("InfosHebdo-console.exe"), [console])


class TestSourceCommandPrefix(unittest.TestCase):
    def test_source_mode_runs_the_launcher_with_utf8(self):
        with mock.patch.object(paths, "FROZEN", False):
            prefix = paths.command_prefix()
        self.assertEqual(prefix[-1], str(paths.LAUNCHER))
        self.assertIn("-X", prefix)


if __name__ == "__main__":
    unittest.main()
