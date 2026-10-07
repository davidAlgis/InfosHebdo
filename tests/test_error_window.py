"""Tests de la fenetre d'erreur et de son branchement dans `auto`.

L'application n'a pas de console : si la collecte echoue, la fenetre est le
seul moyen pour l'utilisateur de le savoir. On verrouille donc :

* qu'elle s'ouvre quand un probleme existe, et seulement alors ;
* qu'elle montre le message exact de chaque source ;
* qu'une erreur imprevue (pas seulement une source en panne) l'ouvre aussi ;
* qu'une fenetre impossible a afficher ne masque jamais l'erreur d'origine.
"""
from __future__ import annotations

import argparse
import copy
import logging
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from infoshebdo import auto, cli
from infoshebdo.config import DEFAULTS, Config
from infoshebdo.ui import error_window


def problems() -> list[auto.Problem]:
    return [
        auto.Problem("Allocine - box-office France", "FetchError: 404 https://exemple/sem-2026"),
        auto.Problem("Steam - meilleures ventes", "ReadTimeout: delai depasse\nsecond ligne"),
    ]


class TestFormatting(unittest.TestCase):
    def test_heading_is_singular_or_plural(self):
        self.assertEqual(error_window.heading(1), "1 probleme a ete rencontre")
        self.assertEqual(error_window.heading(3), "3 problemes ont ete rencontres")

    def test_details_list_every_source_and_message(self):
        text = error_window.format_details(problems(), "Explication.")
        self.assertIn("Explication.", text)
        self.assertIn("- Allocine - box-office France", text)
        self.assertIn("FetchError: 404", text)
        self.assertIn("- Steam - meilleures ventes", text)
        self.assertIn("second ligne", text)

    def test_missing_message_is_said_explicitly(self):
        text = error_window.format_details([auto.Problem("Source", "")])
        self.assertIn("(aucun detail fourni)", text)


class TestWindow(unittest.TestCase):
    """Construit la vraie fenetre, sans l'afficher. Ignore sans ecran."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - environnement sans affichage
            self.skipTest(f"Tk indisponible : {exc}")
        self.root.withdraw()
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            for identifier in self.root.tk.call("after", "info"):
                self.root.tk.call("after", "cancel", identifier)
            self.root.destroy()
        except tk.TclError:
            pass

    def _window(self, **kwargs) -> error_window.ErrorWindow:
        return error_window.ErrorWindow(
            problems(), explanation="Explication.", root=self.root, **kwargs
        )

    def test_shows_each_source_and_its_message(self):
        window = self._window()
        content = window.text.get("1.0", "end")
        self.assertIn("Allocine - box-office France", content)
        self.assertIn("FetchError: 404", content)
        self.assertIn("ReadTimeout: delai depasse", content)

    def test_text_is_read_only_but_selectable(self):
        window = self._window()
        self.assertEqual(str(window.text.cget("state")), "disabled")

    def test_buttons_follow_what_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "rapport.html"
            report.write_text("<html></html>", encoding="utf-8")
            window = self._window(report_path=report, log_path=Path(tmp) / "absent.log")
        self.assertNotIn("disabled", window.buttons["report"].state())
        self.assertIn("disabled", window.buttons["log"].state())

    def test_retry_button_exists_only_when_a_callback_is_given(self):
        self.assertIn("disabled", self._window().buttons["retry"].state())
        window = self._window(on_retry=lambda: None)
        self.assertNotIn("disabled", window.buttons["retry"].state())

    def test_retry_closes_the_window_then_runs_the_callback(self):
        calls = []
        window = self._window(on_retry=lambda: calls.append("relance"))
        window.retry()
        self.assertEqual(calls, ["relance"])
        with self.assertRaises(tk.TclError):         # la fenetre n'existe plus
            self.root.winfo_exists()

    def test_embedded_window_does_not_close_its_parent(self):
        window = error_window.ErrorWindow(problems(), explanation="x", parent=self.root)
        window.present()
        self.assertTrue(window.embedded)
        window.root.destroy()
        self.assertTrue(self.root.winfo_exists())

    def test_report_button_disabled_without_report(self):
        window = self._window(report_path=None, log_path=None)
        self.assertIn("disabled", window.buttons["report"].state())

    def test_copy_puts_the_details_on_the_clipboard(self):
        window = self._window()
        window.copy_details()
        copied = self.root.clipboard_get()
        self.assertIn("FetchError: 404", copied)
        self.assertIn("Explication.", copied)

    def test_a_failing_button_does_not_close_the_window(self):
        window = self._window(report_path=Path("inexistant.html"))
        with mock.patch.object(error_window, "open_in_explorer", side_effect=OSError("boom")):
            window.open_report()          # ne doit pas lever
        self.assertTrue(self.root.winfo_exists())


def make_args(**overrides) -> argparse.Namespace:
    values = dict(date=None, force=False, no_window=False)
    values.update(overrides)
    return argparse.Namespace(**values)


class TestAutoCommandShowsTheWindow(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(raw=copy.deepcopy(DEFAULTS))
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        # Pas de vraie fenetre, pas de vraie sortie console.
        patcher = mock.patch.object(error_window, "show")
        self.show = patcher.start()
        self.addCleanup(patcher.stop)
        for stream in ("stdout", "stderr"):
            p = mock.patch(f"sys.{stream}", new=mock.MagicMock())
            p.start()
            self.addCleanup(p.stop)

    def _run_with(self, result=None, error=None, **args) -> int:
        patch_run = mock.patch.object(
            auto, "run", side_effect=error, return_value=result
        )
        with patch_run:
            return cli.cmd_auto(make_args(**args), self.cfg)

    def test_window_opens_when_a_source_failed(self):
        result = auto.AutoResult(collected=True, problems=problems(), opened=True)
        code = self._run_with(result)
        self.assertEqual(code, 1)
        self.show.assert_called_once()
        shown_problems = self.show.call_args.args[0]
        self.assertEqual([p.source for p in shown_problems], [p.source for p in problems()])
        self.assertEqual(self.show.call_args.kwargs["explanation"], result.explanation)

    def test_no_window_when_everything_worked(self):
        code = self._run_with(auto.AutoResult(collected=True, opened=True))
        self.assertEqual(code, 0)
        self.show.assert_not_called()

    def test_no_window_flag_suppresses_it(self):
        result = auto.AutoResult(collected=True, problems=problems())
        code = self._run_with(result, no_window=True)
        self.assertEqual(code, 1)
        self.show.assert_not_called()

    def test_unexpected_crash_opens_the_window_with_the_traceback(self):
        code = self._run_with(error=RuntimeError("base verrouillee"))
        self.assertEqual(code, 2)
        self.show.assert_called_once()
        (shown,) = self.show.call_args.args[0]
        self.assertEqual(shown.source, "Erreur inattendue")
        self.assertIn("RuntimeError: base verrouillee", shown.message)
        self.assertIn("Traceback", shown.message)

    def test_unavailable_window_never_hides_the_original_error(self):
        # Sans ecran (cron, session distante) Tk leve : le code retour doit
        # rester celui de l'echec de collecte, pas une exception nouvelle.
        self.show.side_effect = tk.TclError("no display")
        result = auto.AutoResult(collected=True, problems=problems())
        self.assertEqual(self._run_with(result), 1)


if __name__ == "__main__":
    unittest.main()
