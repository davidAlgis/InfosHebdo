"""Tests de la logique de l'application residente (sans fenetre).

L'utilisateur ne veut rien lancer : l'application s'inscrit seule au demarrage,
ouvre le rapport au premier lancement puis une fois par semaine. Ces tests
verrouillent les decisions, pas l'affichage :

* l'inscription au demarrage se fait une fois, et un refus de l'utilisateur
  est definitif ;
* le premier lancement est traite comme la premiere utilisation de la semaine ;
* les deux actions manuelles font ce qu'elles annoncent ;
* hors ligne, la fenetre d'erreur n'est pas rouverte toutes les 30 minutes.
"""
from __future__ import annotations

import copy
import logging
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from infoshebdo import auto, db, paths, resident, startup, viewer
from infoshebdo.config import DEFAULTS, Config
from infoshebdo.pipeline import CollectorOutcome, CollectRun

MONDAY = date(2030, 10, 7)
TUESDAY = date(2030, 10, 8)


class ResidentCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        tmp = Path(self._dir.name)
        for patcher in (
            mock.patch.dict(os.environ, {"INFOSHEBDO_DB": str(tmp / "t.sqlite3")}),
            mock.patch.object(paths, "REPORTS_DIR", tmp / "reports"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        os.environ.pop(resident.NO_AUTOSTART_ENV, None)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db.init()
        self.config = Config(raw=copy.deepcopy(DEFAULTS))


class FakeRegistry:
    """Remplace le registre Windows : rien n'est inscrit sur la vraie machine."""

    def __init__(self, supported: bool = True, enabled: bool = False, matches: bool = True):
        self.supported = supported
        self.state = startup.StartupState(enabled=enabled, command="x", matches_current=matches)
        self.enable_calls = 0
        self.disable_calls = 0

    def apply(self, case: unittest.TestCase) -> "FakeRegistry":
        for name, value in (
            ("is_supported", lambda: self.supported),
            ("status", lambda: self.state),
            ("enable", self._enable),
            ("disable", self._disable),
        ):
            patcher = mock.patch.object(startup, name, value)
            patcher.start()
            case.addCleanup(patcher.stop)
        return self

    def _enable(self):
        self.enable_calls += 1
        self.state = startup.StartupState(enabled=True, command="x", matches_current=True)
        return "commande"

    def _disable(self):
        self.disable_calls += 1
        was = self.state.enabled
        self.state = startup.StartupState(enabled=False)
        return was


class TestAutostart(ResidentCase):
    def test_first_launch_registers_itself(self):
        registry = FakeRegistry(enabled=False).apply(self)
        self.assertEqual(resident.ensure_autostart(), "enabled")
        self.assertEqual(registry.enable_calls, 1)

    def test_already_registered_is_left_alone(self):
        registry = FakeRegistry(enabled=True, matches=True).apply(self)
        self.assertEqual(resident.ensure_autostart(), "already")
        self.assertEqual(registry.enable_calls, 0)

    def test_moved_application_updates_the_entry(self):
        # Reinstallation dans un autre dossier : l'entree pointait ailleurs.
        registry = FakeRegistry(enabled=True, matches=False).apply(self)
        self.assertEqual(resident.ensure_autostart(), "updated")
        self.assertEqual(registry.enable_calls, 1)

    def test_a_refusal_by_the_user_is_final(self):
        registry = FakeRegistry(enabled=False).apply(self)
        resident.set_autostart(False)                    # case decochee dans le menu
        self.assertEqual(resident.ensure_autostart(), "disabled-by-user")
        self.assertEqual(registry.enable_calls, 0)

    def test_the_user_can_turn_it_back_on(self):
        registry = FakeRegistry(enabled=False).apply(self)
        resident.set_autostart(False)
        resident.set_autostart(True)
        self.assertTrue(registry.state.enabled)
        self.assertEqual(resident.ensure_autostart(), "already")

    def test_environment_variable_disables_registration(self):
        registry = FakeRegistry(enabled=False).apply(self)
        with mock.patch.dict(os.environ, {resident.NO_AUTOSTART_ENV: "1"}):
            self.assertEqual(resident.ensure_autostart(), "disabled-by-env")
        self.assertEqual(registry.enable_calls, 0)

    def test_unsupported_system_is_not_an_error(self):
        FakeRegistry(supported=False).apply(self)
        self.assertEqual(resident.ensure_autostart(), "unsupported")

    def test_registry_failure_never_prevents_startup(self):
        FakeRegistry(enabled=False).apply(self)
        with mock.patch.object(startup, "enable", side_effect=startup.StartupError("refuse")):
            outcome = resident.ensure_autostart()
        self.assertTrue(outcome.startswith("error"))


class TestDailyNotice(unittest.TestCase):
    def test_one_notice_per_day(self):
        notice = resident.DailyNotice()
        self.assertTrue(notice.allow(MONDAY))
        self.assertFalse(notice.allow(MONDAY))      # 30 minutes plus tard, toujours hors ligne
        self.assertFalse(notice.allow(MONDAY))
        self.assertTrue(notice.allow(TUESDAY))      # nouveau jour : on previent a nouveau


class TestFirstLaunch(ResidentCase):
    """« Au premier lancement, c'est la premiere utilisation de la semaine. »"""

    def _collect(self, config, today=None):
        with db.session() as conn:
            run_id = db.start_run(conn, "collect")
            db.log_collector(conn, run_id, "faux", "Faux", "ok", 1, 1)
            db.finish_run(conn, run_id, "ok")
        return CollectRun(run_id=run_id)

    def test_fresh_install_opens_the_report_whatever_the_weekday(self):
        opened = []
        # Un jeudi, base toute neuve, rien n'a jamais ete montre.
        result = auto.run(
            self.config, today=date(2030, 10, 10),
            collect_fn=self._collect, open_fn=lambda p: opened.append(p) or True,
        )
        self.assertTrue(result.opened)
        self.assertEqual(len(opened), 1)

    def test_then_it_waits_for_next_week(self):
        opened = []
        open_fn = lambda p: opened.append(p) or True        # noqa: E731
        auto.run(self.config, today=date(2030, 10, 10), collect_fn=self._collect, open_fn=open_fn)
        for day in range(11, 14):                            # vendredi, samedi, dimanche
            auto.run(self.config, today=date(2030, 10, day), collect_fn=self._collect, open_fn=open_fn)
        self.assertEqual(len(opened), 1)
        auto.run(self.config, today=date(2030, 10, 14), collect_fn=self._collect, open_fn=open_fn)
        self.assertEqual(len(opened), 2)                    # le lundi suivant

    def test_a_session_left_open_over_the_weekend_opens_on_monday(self):
        # La minuterie de 30 minutes rappelle `auto.run` : la nouvelle semaine suffit.
        opened = []
        open_fn = lambda p: opened.append(p) or True        # noqa: E731
        auto.run(self.config, today=date(2030, 10, 6), collect_fn=self._collect, open_fn=open_fn)
        auto.run(self.config, today=date(2030, 10, 6), collect_fn=self._collect, open_fn=open_fn)
        self.assertEqual(len(opened), 1)
        auto.run(self.config, today=MONDAY, collect_fn=self._collect, open_fn=open_fn)
        self.assertEqual(len(opened), 2)


class TestManualActions(ResidentCase):
    def _run(self, statuses=("ok",)) -> CollectRun:
        run = CollectRun(run_id=1)
        for index, status in enumerate(statuses):
            run.outcomes.append(
                CollectorOutcome(
                    name=f"s{index}", label=f"Source {index}", status=status,
                    rows=5 if status == "ok" else 0,
                    error="ConnectionError: hors ligne" if status == "erreur" else "",
                )
            )
        return run

    def test_search_reports_what_was_collected(self):
        result = resident.search_data(self.config, collect_fn=lambda c, today=None: self._run())
        self.assertEqual(result.problems, [])
        self.assertIn("5 observation(s)", result.message)

    def test_search_failure_lists_each_source_with_its_message(self):
        result = resident.search_data(
            self.config, collect_fn=lambda c, today=None: self._run(("ok", "erreur"))
        )
        self.assertEqual([p.source for p in result.problems], ["Source 1"])
        self.assertIn("hors ligne", result.problems[0].message)
        self.assertFalse(result.all_failed)
        self.assertIn("1 probleme", result.message)

    def test_search_total_failure_says_to_check_the_connection(self):
        result = resident.search_data(
            self.config, collect_fn=lambda c, today=None: self._run(("erreur", "erreur"))
        )
        self.assertTrue(result.all_failed)
        self.assertIn("connexion", result.explanation)

    def test_open_report_rebuilds_it_from_the_database(self):
        opened = []
        result = resident.open_report_now(
            self.config, today=MONDAY, open_fn=lambda p: opened.append(p) or True
        )
        self.assertTrue(result.opened)
        self.assertEqual(opened, [result.path])
        self.assertEqual(result.path.name, "infoshebdo-2030-10-07.html")
        self.assertTrue(result.path.exists())

    def test_open_report_works_even_with_no_report_yet(self):
        # Il n'y a pas de cas « aucun rapport » : il est toujours reconstruit.
        self.assertIsNone(viewer.latest_report())
        result = resident.open_report_now(self.config, today=MONDAY, open_fn=lambda p: True)
        self.assertTrue(result.path.exists())

    def test_opening_by_hand_counts_as_seen(self):
        # Sinon le rapport automatique de la semaine s'ouvrirait derriere.
        resident.open_report_now(self.config, today=MONDAY, open_fn=lambda p: True)
        with db.session() as conn:
            self.assertTrue(viewer.already_shown(conn, TUESDAY))

    def test_browser_failure_is_reported_and_does_not_count_as_seen(self):
        result = resident.open_report_now(self.config, today=MONDAY, open_fn=lambda p: False)
        self.assertFalse(result.opened)
        self.assertIn("navigateur", result.message)
        with db.session() as conn:
            self.assertFalse(viewer.already_shown(conn, MONDAY))


class TestStatusLines(ResidentCase):
    def test_nothing_done_yet(self):
        lines = resident.status_lines(MONDAY)
        self.assertEqual(lines[0], "Derniere recherche des donnees : jamais")
        self.assertIn("ouverture automatique prevue", lines[1])

    def test_after_a_collection_and_a_report(self):
        with db.session() as conn:
            run_id = db.start_run(conn, "collect")
            db.log_collector(conn, run_id, "x", "X", "ok", 1, 1)
            db.finish_run(conn, run_id, "ok")
            viewer.mark_shown(conn, date.today())
        lines = resident.status_lines(date.today())
        self.assertIn("aujourd'hui a", lines[0])
        self.assertIn("deja ouvert", lines[1])

    def test_an_old_collection_shows_its_date(self):
        with db.session() as conn:
            run_id = db.start_run(conn, "collect")
            db.log_collector(conn, run_id, "x", "X", "ok", 1, 1)
            db.finish_run(conn, run_id, "ok")
        later = date.today() + timedelta(days=3)
        self.assertRegex(resident.status_lines(later)[0], r"le \d\d/\d\d/\d{4} a \d\d:\d\d")


if __name__ == "__main__":
    unittest.main()
