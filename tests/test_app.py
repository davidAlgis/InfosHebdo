"""Tests de la fenetre principale, sur une vraie fenetre Tk (jamais affichee).

La logique est testee ailleurs (`test_resident`, `test_auto`). Ici on verifie le
cablage : ce que les deux boutons declenchent, ce qui se passe a la fin d'un
traitement, et surtout que la fenetre d'erreur apparait meme quand la fenetre
principale est repliee dans la zone de notification. Ignores sans affichage.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
import tkinter as tk
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from infoshebdo import auto, db, paths, resident
from infoshebdo.ui import app as app_module


def cancel_pending_callbacks(root) -> None:
    """Annule les `after` en attente : sinon ils se declenchent apres la
    destruction de la fenetre et polluent la sortie des tests suivants."""
    try:
        for identifier in root.tk.call("after", "info"):
            root.tk.call("after", "cancel", identifier)
    except tk.TclError:
        pass


class FakeTray:
    """Remplace pystray : aucune icone n'apparait pendant les tests."""

    ready = True

    def __init__(self, commands, autostart_checked=None):
        self.commands = commands
        self.autostart_checked = autostart_checked
        self.notifications: list[str] = []
        self.menu_refreshed = 0

    def start(self) -> bool:
        return type(self).ready

    def notify(self, message, title="InfosHebdo") -> None:
        self.notifications.append(message)

    def refresh_menu(self) -> None:
        self.menu_refreshed += 1

    def stop(self) -> None:
        pass


def problem_list() -> list[auto.Problem]:
    return [auto.Problem("Allocine", "FetchError: injoignable")]


class AppCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        tmp = Path(self._dir.name)
        for patcher in (
            mock.patch.dict(os.environ, {
                "INFOSHEBDO_DB": str(tmp / "t.sqlite3"),
                resident.NO_AUTOSTART_ENV: "1",
            }),
            mock.patch.object(paths, "REPORTS_DIR", tmp / "reports"),
            mock.patch.object(app_module, "TrayIcon", FakeTray),
            mock.patch.object(app_module, "STARTUP_DELAY_MS", 10**9),   # on appelle _startup a la main
            mock.patch.object(app_module, "TICK_MS", 10**9),
            mock.patch.object(app_module.messagebox, "showinfo"),
            mock.patch.object(app_module.messagebox, "showwarning"),
            mock.patch.object(app_module.messagebox, "showerror"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        FakeTray.ready = True
        db.init()
        try:
            self.app = app_module.App()
        except tk.TclError as exc:  # pragma: no cover - environnement sans affichage
            self.skipTest(f"Tk indisponible : {exc}")
        self.app.withdraw()
        self.addCleanup(self._close)

    def _close(self):
        cancel_pending_callbacks(self.app)
        try:
            self.app.update_idletasks()         # laisse ttk finir un changement de theme
            self.app._quitting = True
            self.app.destroy()
        except tk.TclError:
            pass

    def pump(self, until, timeout: float = 8.0) -> None:
        """Fait tourner la boucle Tk jusqu'a ce que `until()` soit vrai."""
        deadline = time.monotonic() + timeout
        while not until():
            if time.monotonic() > deadline:
                self.fail("delai depasse en attendant la fin du traitement")
            self.app.update()
            time.sleep(0.01)

    def idle(self):
        return not self.app.runner.busy and self.app.runner.queue.empty()

    def settle(self):
        """Attend la fin du traitement ET le traitement de son message."""
        self.pump(lambda: not self.app.runner.busy)
        for _ in range(10):
            self.app.update()
            time.sleep(0.02)

    def error_windows(self):
        return [w for w in self.app._error_windows if w.root.winfo_exists()]


class TestLayout(AppCase):
    def test_only_two_actions_are_offered(self):
        buttons = [
            child for child in self._descendants(self.app)
            if child.winfo_class() == "TButton"
        ]
        self.assertEqual(
            sorted(b.cget("text") for b in buttons),
            ["Ouvrir le rapport", "Rechercher les donnees"],
        )

    def test_there_are_no_tabs_or_settings(self):
        classes = {child.winfo_class() for child in self._descendants(self.app)}
        self.assertNotIn("TNotebook", classes)
        self.assertNotIn("Text", classes)

    def test_status_lines_are_filled_in(self):
        texts = [label.cget("text") for label in self.app.status_lines]
        self.assertTrue(texts[0].startswith("Derniere recherche des donnees"))
        self.assertTrue(texts[1].startswith("Rapport de cette semaine"))

    def _descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self._descendants(child)


class TestAutomaticCheck(AppCase):
    def test_startup_registers_autostart_then_checks_once(self):
        result = auto.AutoResult(collected=True, opened=True)
        with mock.patch.object(resident, "ensure_autostart", return_value="enabled") as ensure, \
             mock.patch.object(auto, "run", return_value=result) as run:
            self.app._startup()
            self.settle()
        ensure.assert_called_once()
        run.assert_called_once()

    def test_after_the_first_check_the_window_goes_to_the_tray(self):
        self.app.deiconify()
        self.app.update()
        with mock.patch.object(auto, "run", return_value=auto.AutoResult(opened=True)):
            self.app.run_auto()
            self.settle()
        self.assertEqual(self.app.state(), "withdrawn")

    def test_without_a_tray_the_window_is_not_hidden_away(self):
        FakeTray.ready = False
        self.app._tray_ready = self.app.tray.start()
        self.app.deiconify()
        with mock.patch.object(auto, "run", return_value=auto.AutoResult(opened=True)):
            self.app.run_auto()
            self.settle()
        self.assertNotEqual(self.app.state(), "withdrawn")

    def test_a_failed_check_opens_the_error_window_even_when_hidden(self):
        result = auto.AutoResult(collected=True, all_failed=True, problems=problem_list())
        self.assertEqual(self.app.state(), "withdrawn")
        with mock.patch.object(auto, "run", return_value=result):
            self.app.run_auto()
            self.settle()
        windows = self.error_windows()
        self.assertEqual(len(windows), 1)
        self.assertTrue(windows[0].root.winfo_viewable())      # visible malgre la fenetre repliee
        self.assertIn("Allocine", windows[0].text.get("1.0", "end"))

    def test_offline_does_not_reopen_the_window_every_30_minutes(self):
        result = auto.AutoResult(collected=True, all_failed=True, problems=problem_list())
        with mock.patch.object(auto, "run", return_value=result):
            for _ in range(3):
                self.app.run_auto()
                self.settle()
        self.assertEqual(len(self.error_windows()), 1)

    def test_a_new_day_warns_again(self):
        result = auto.AutoResult(collected=True, all_failed=True, problems=problem_list())
        with mock.patch.object(auto, "run", return_value=result):
            self.app.run_auto()
            self.settle()
            self.app.notice._day = date(2000, 1, 1)             # « hier »
            self.app.run_auto()
            self.settle()
        self.assertEqual(len(self.error_windows()), 2)

    def test_a_clean_check_shows_no_window(self):
        with mock.patch.object(auto, "run", return_value=auto.AutoResult(collected=True, opened=True)):
            self.app.run_auto()
            self.settle()
        self.assertEqual(self.error_windows(), [])

    def test_the_periodic_tick_reschedules_itself(self):
        with mock.patch.object(auto, "run", return_value=auto.AutoResult()), \
             mock.patch.object(self.app, "after") as after:
            self.app._tick()
        after.assert_called_once()
        self.assertEqual(after.call_args.args[0], app_module.TICK_MS)

    def test_the_check_is_skipped_while_another_job_runs(self):
        with mock.patch.object(auto, "run") as run, \
             mock.patch.object(self.app.runner, "submit", return_value=False):
            self.app.run_auto()
        run.assert_not_called()


class TestSearchButton(AppCase):
    def test_runs_a_collection_and_shows_its_result(self):
        result = resident.SearchResult(message="727 observation(s), 0 article(s)")
        with mock.patch.object(resident, "search_data", return_value=result) as search:
            self.app.search_button.invoke()
            self.settle()
        search.assert_called_once()
        self.assertIn("727 observation(s)", self.app.message.cget("text"))
        self.assertEqual(self.error_windows(), [])
        self.assertTrue(self.app.tray.notifications)

    def test_failure_always_opens_the_window_and_offers_a_retry(self):
        result = resident.SearchResult(message="x", problems=problem_list(), all_failed=True)
        with mock.patch.object(resident, "search_data", return_value=result):
            self.app.search_button.invoke()
            self.settle()
            self.app.search_button.invoke()                      # 2e fois : toujours montre
            self.settle()
        windows = self.error_windows()
        self.assertEqual(len(windows), 2)
        self.assertNotIn("disabled", windows[0].buttons["retry"].state())

    def test_retry_runs_the_search_again(self):
        result = resident.SearchResult(message="x", problems=problem_list())
        with mock.patch.object(resident, "search_data", return_value=result) as search:
            self.app.search_button.invoke()
            self.settle()
            self.error_windows()[0].retry()
            self.settle()
        self.assertEqual(search.call_count, 2)

    def test_buttons_are_disabled_while_working(self):
        with mock.patch.object(resident, "search_data", side_effect=lambda c: time.sleep(0.4)
                               or resident.SearchResult(message="ok")):
            self.app.search_button.invoke()
            self.app.update()
            self.assertIn("disabled", self.app.search_button.state())
            self.assertIn("disabled", self.app.report_button.state())
            self.settle()
        self.assertNotIn("disabled", self.app.search_button.state())

    def test_a_second_click_while_busy_is_refused_politely(self):
        with mock.patch.object(resident, "search_data", side_effect=lambda c: time.sleep(0.4)
                               or resident.SearchResult(message="ok")):
            self.app.search_data()
            self.app.update()
            self.app.open_report()                               # pendant la recherche
            self.settle()
        app_module.messagebox.showinfo.assert_called()


class TestReportButton(AppCase):
    def test_opens_the_report(self):
        result = resident.OpenResult(path=Path("r.html"), opened=True)
        with mock.patch.object(resident, "open_report_now", return_value=result) as open_now:
            self.app.report_button.invoke()
            self.settle()
        open_now.assert_called_once()
        app_module.messagebox.showwarning.assert_not_called()

    def test_browser_failure_is_explained(self):
        result = resident.OpenResult(path=Path("r.html"), opened=False)
        with mock.patch.object(resident, "open_report_now", return_value=result):
            self.app.report_button.invoke()
            self.settle()
        app_module.messagebox.showwarning.assert_called_once()

    def test_unexpected_exception_is_shown_not_swallowed(self):
        with mock.patch.object(resident, "open_report_now", side_effect=RuntimeError("base verrouillee")):
            self.app.report_button.invoke()
            self.settle()
        app_module.messagebox.showerror.assert_called_once()
        self.assertIn("base verrouillee", app_module.messagebox.showerror.call_args.args[1])


class TestTrayCommands(AppCase):
    def test_menu_actions_trigger_the_same_jobs(self):
        with mock.patch.object(resident, "search_data",
                               return_value=resident.SearchResult(message="ok")) as search, \
             mock.patch.object(resident, "open_report_now",
                               return_value=resident.OpenResult(path=Path("r.html"), opened=True)) as opened:
            self.app.tray_commands.put("search")
            self.app._poll()
            self.settle()
            self.app.tray_commands.put("report")
            self.app._poll()
            self.settle()
        search.assert_called_once()
        opened.assert_called_once()

    def test_show_command_brings_the_window_back(self):
        self.assertEqual(self.app.state(), "withdrawn")
        self.app.tray_commands.put("show")
        self.app._poll()
        self.assertEqual(self.app.state(), "normal")

    def test_a_second_instance_shows_the_window(self):
        single = mock.MagicMock()
        app = app_module.App(single=single)
        self.addCleanup(lambda: (cancel_pending_callbacks(app), setattr(app, "_quitting", True), app.destroy()))
        app.withdraw()
        single.on_show()                                         # l'instance cherche a se montrer
        app._poll()
        self.assertEqual(app.state(), "normal")

    def test_autostart_toggle_flips_the_current_state(self):
        with mock.patch.object(resident, "autostart_enabled", return_value=True), \
             mock.patch.object(resident, "set_autostart") as set_it:
            self.app._toggle_autostart()
        set_it.assert_called_once_with(False)
        self.assertEqual(self.app.tray.menu_refreshed, 1)

    def test_autostart_toggle_failure_is_reported(self):
        with mock.patch.object(resident, "autostart_enabled", return_value=False), \
             mock.patch.object(resident, "set_autostart", side_effect=OSError("registre refuse")):
            self.app._toggle_autostart()
        app_module.messagebox.showerror.assert_called_once()


class TestClosing(AppCase):
    def test_the_cross_hides_instead_of_quitting_when_there_is_a_tray(self):
        self.app.deiconify()
        self.app._on_close()
        self.assertEqual(self.app.state(), "withdrawn")
        self.assertFalse(self.app._quitting)

    def test_the_cross_quits_when_there_is_no_tray(self):
        self.app._tray_ready = False
        self.app._on_close()
        self.assertTrue(self.app._quitting)

    def test_quit_releases_the_single_instance_port(self):
        single = mock.MagicMock()
        self.app.single = single
        self.app._quit_now()
        single.close.assert_called_once()

    def test_quit_asks_before_interrupting_a_running_job(self):
        with mock.patch.object(app_module.messagebox, "askyesno", return_value=False) as ask, \
             mock.patch.object(type(self.app.runner), "busy", new_callable=mock.PropertyMock,
                               return_value=True):
            self.app._quit_now()
        ask.assert_called_once()
        self.assertFalse(self.app._quitting)


if __name__ == "__main__":
    unittest.main()
