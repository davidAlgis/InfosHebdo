"""Tests de la commande planifiee : « collecte du jour, rapport de la semaine ».

Ni reseau ni navigateur : la collecte et l'ouverture sont remplacees par des
faux, et la base est temporaire. Ce qu'on verrouille, c'est la regle metier :

* le rapport s'ouvre UNE fois par semaine ISO, le premier jour ou la tache
  s'execute (lundi si le poste est allume, sinon mardi...) ;
* la collecte, elle, n'a lieu qu'une fois par jour, meme si la tache se
  declenche plusieurs fois (quotidien + ouverture de session) ;
* un navigateur qui ne s'ouvre pas ne doit pas faire perdre le rapport de la
  semaine : on retentera au declenchement suivant.
"""
from __future__ import annotations

import copy
import logging
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from infoshebdo import auto, db, paths, viewer
from infoshebdo.config import DEFAULTS, Config
from infoshebdo.pipeline import CollectorOutcome, CollectRun

# Lundi et jours voisins de la semaine ISO 2026-W41, puis la suivante.
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
WEDNESDAY = date(2026, 10, 7)
NEXT_MONDAY = date(2026, 10, 12)


class TestWeekKey(unittest.TestCase):
    def test_whole_week_shares_a_key(self):
        keys = {viewer.week_key(date(2026, 10, d)) for d in range(5, 12)}
        self.assertEqual(keys, {"2026-W41"})

    def test_monday_starts_a_new_week(self):
        self.assertEqual(viewer.week_key(NEXT_MONDAY), "2026-W42")

    def test_iso_year_differs_from_calendar_year(self):
        # Le 1er janvier 2027 appartient a la semaine 53 de 2026, et le
        # 29 decembre 2025 a la semaine 1 de 2026.
        self.assertEqual(viewer.week_key(date(2027, 1, 1)), "2026-W53")
        self.assertEqual(viewer.week_key(date(2025, 12, 29)), "2026-W01")


class AutoCase(unittest.TestCase):
    """Base temporaire, dossier de rapports temporaire, faux collecteur."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        tmp = Path(self._dir.name)
        env = mock.patch.dict(os.environ, {"INFOSHEBDO_DB": str(tmp / "test.sqlite3")})
        reports = mock.patch.object(paths, "REPORTS_DIR", tmp / "reports")
        env.start()
        reports.start()
        self.addCleanup(env.stop)
        self.addCleanup(reports.stop)
        self.addCleanup(self._dir.cleanup)

        # Les avertissements attendus (base vide) n'ont rien a faire dans la sortie.
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, logging.NOTSET)

        db.init()
        self.config = Config(raw=copy.deepcopy(DEFAULTS))
        self.collects = 0
        self.opened: list[Path] = []
        self.browser_works = True

    # -- doubles ------------------------------------------------------- #
    def collect(self, config, today=None, statuses=("ok",)) -> CollectRun:
        """Faux collecteur : ecrit un vrai journal de collecte, sans reseau.

        `statuses` : un statut par source simulee ('ok', 'vide', 'erreur').
        """
        self.collects += 1
        with db.session() as conn:
            run_id = db.start_run(conn, "collect")
            for index, status in enumerate(statuses):
                db.log_collector(conn, run_id, f"faux{index}", f"Faux {index}", status, 1, 1)
            db.finish_run(conn, run_id, "erreur" if "erreur" in statuses else "ok")
        run = CollectRun(run_id=run_id)
        for index, status in enumerate(statuses):
            run.outcomes.append(
                CollectorOutcome(
                    name=f"faux{index}",
                    label=f"Faux {index}",
                    status=status,
                    error="TimeoutError: delai depasse" if status == "erreur" else "",
                )
            )
        return run

    def offline(self, config, today=None) -> CollectRun:
        """Poste sans reseau : toutes les sources echouent."""
        return self.collect(config, today, statuses=("erreur", "erreur"))

    def partial(self, config, today=None) -> CollectRun:
        """Une source sur deux repond."""
        return self.collect(config, today, statuses=("ok", "erreur"))

    def open_report(self, path: Path) -> bool:
        if self.browser_works:
            self.opened.append(path)
        return self.browser_works

    def go(self, day: date, **kwargs) -> auto.AutoResult:
        return auto.run(
            self.config,
            today=day,
            collect_fn=kwargs.pop("collect_fn", self.collect),
            open_fn=self.open_report,
            **kwargs,
        )


class TestWeeklyOpening(AutoCase):
    def test_opens_on_the_first_run_of_the_week(self):
        result = self.go(MONDAY)
        self.assertTrue(result.opened)
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.opened[0].exists())
        self.assertEqual(self.opened[0].suffix, ".html")

    def test_second_run_the_same_day_does_not_reopen(self):
        self.go(MONDAY)
        result = self.go(MONDAY)
        self.assertFalse(result.opened)
        self.assertEqual(result.reason, "rapport de la semaine deja ouvert")
        self.assertEqual(len(self.opened), 1)

    def test_no_reopen_later_in_the_same_week(self):
        self.go(MONDAY)
        self.go(TUESDAY)
        self.go(WEDNESDAY)
        self.assertEqual(len(self.opened), 1)

    def test_missed_monday_opens_on_tuesday(self):
        # Le poste est eteint le lundi : premier lancement le mardi.
        result = self.go(TUESDAY)
        self.assertTrue(result.opened)
        self.go(WEDNESDAY)
        self.assertEqual(len(self.opened), 1)

    def test_next_week_opens_again(self):
        self.go(MONDAY)
        result = self.go(NEXT_MONDAY)
        self.assertTrue(result.opened)
        self.assertEqual(len(self.opened), 2)

    def test_force_reopens_within_the_week(self):
        self.go(MONDAY)
        result = self.go(TUESDAY, force=True)
        self.assertTrue(result.opened)
        self.assertEqual(len(self.opened), 2)

    def test_disabled_auto_open_never_opens(self):
        self.config.raw["report"]["auto_open"] = False
        result = self.go(MONDAY)
        self.assertFalse(result.opened)
        self.assertEqual(self.opened, [])
        # La collecte, elle, a quand meme lieu.
        self.assertEqual(self.collects, 1)

    def test_report_file_is_written_even_when_empty_database(self):
        result = self.go(MONDAY)
        self.assertEqual(result.report_path.name, "infoshebdo-2026-10-05.html")
        html = result.report_path.read_text(encoding="utf-8")
        self.assertIn("<!doctype html>", html)


class TestBrowserFailure(AutoCase):
    def test_failure_is_reported_and_not_remembered(self):
        self.browser_works = False
        result = self.go(MONDAY)
        self.assertFalse(result.opened)
        self.assertEqual(result.reason, "ouverture impossible")
        self.assertFalse(result.ok)  # code retour non nul : visible dans le Planificateur

        # Au declenchement suivant, tout va bien : le rapport n'est pas perdu.
        self.browser_works = True
        retry = self.go(TUESDAY)
        self.assertTrue(retry.opened)
        self.assertEqual(len(self.opened), 1)


class TestDailyCollection(AutoCase):
    """Ici on utilise la vraie date du jour : `collected_on` lit l'horloge."""

    def test_collects_once_per_day_however_often_triggered(self):
        today = date.today()
        first = self.go(today)
        second = self.go(today)
        third = self.go(today)
        self.assertTrue(first.collected)
        self.assertFalse(second.collected)
        self.assertFalse(third.collected)
        self.assertEqual(self.collects, 1)

    def test_collects_even_when_the_report_was_already_opened(self):
        # Le rapport est hebdomadaire, la collecte quotidienne.
        today = date.today()
        self.go(today)
        self.assertEqual(self.collects, 1)
        # Lendemain simule : on vide le journal pour que « aujourd'hui » soit
        # a nouveau a collecter, tout en gardant la marque de la semaine.
        with db.session() as conn:
            conn.execute("DELETE FROM collector_runs")
            conn.execute("DELETE FROM runs")
        result = self.go(today)
        self.assertTrue(result.collected)
        self.assertFalse(result.opened)

    def test_offline_run_is_retried(self):
        # Toutes les sources en erreur (poste sans reseau) : ce n'est pas une
        # collecte, le declenchement suivant doit reessayer.
        today = date.today()
        first = self.go(today, collect_fn=self.offline)
        self.assertEqual(first.collect_failures, 2)
        second = self.go(today)
        self.assertTrue(second.collected)
        self.assertEqual(self.collects, 2)


class TestCollectionFailures(AutoCase):
    """Ce que l'utilisateur doit voir quand la collecte ne marche pas."""

    def _html(self, result) -> str:
        return result.report_path.read_text(encoding="utf-8")

    def test_total_failure_still_opens_the_report_with_a_warning(self):
        result = self.go(MONDAY, collect_fn=self.offline)
        self.assertTrue(result.all_failed)
        self.assertTrue(result.opened)
        self.assertEqual(len(self.opened), 1)
        self.assertFalse(result.ok)               # le code retour signale l'echec
        html = self._html(result)
        self.assertIn("Attention", html)
        self.assertIn("a echoue sur toutes les sources", html)

    def test_warning_is_also_in_the_text_version(self):
        result = self.go(MONDAY, collect_fn=self.offline)
        text = result.report_path.with_suffix(".txt").read_text(encoding="utf-8")
        self.assertIn("ATTENTION : La collecte du jour a echoue", text)

    def test_warning_gives_the_date_of_the_last_good_collection(self):
        self.collect(self.config)                 # une collecte reussie en base
        with db.session() as conn:
            expected = db.last_successful_collect(conn)
        self.assertIsNotNone(expected)
        result = self.go(WEDNESDAY, collect_fn=self.offline)
        self.assertIn(f"derniere collecte reussie ({expected:%d/%m/%Y})", self._html(result))

    def test_warning_without_any_previous_collection(self):
        result = self.go(MONDAY, collect_fn=self.offline)
        self.assertIn("une collecte anterieure", self._html(result))

    def test_total_failure_does_not_consume_the_week(self):
        # Rapport de secours ouvert avec un avertissement ; des qu'une collecte
        # aboutit, le rapport a jour s'ouvre a son tour.
        self.go(MONDAY, collect_fn=self.offline)
        fresh = self.go(TUESDAY)                  # le reseau est revenu
        self.assertTrue(fresh.opened)
        self.assertEqual(len(self.opened), 2)
        self.assertNotIn("a echoue sur toutes les sources", self._html(fresh))
        # Et maintenant la semaine est bien consommee.
        self.go(WEDNESDAY)
        self.assertEqual(len(self.opened), 2)

    def test_stale_report_is_not_reopened_on_every_attempt_of_the_same_day(self):
        self.go(MONDAY, collect_fn=self.offline)
        second = self.go(MONDAY, collect_fn=self.offline)   # ouverture de session
        self.assertFalse(second.opened)
        self.assertIn("deja ouvert aujourd'hui", second.reason)
        self.assertEqual(len(self.opened), 1)
        self.assertFalse(second.ok)               # l'erreur reste signalee

    def test_stale_report_opens_again_the_next_day_if_still_offline(self):
        self.go(MONDAY, collect_fn=self.offline)
        result = self.go(TUESDAY, collect_fn=self.offline)
        self.assertTrue(result.opened)
        self.assertEqual(len(self.opened), 2)

    def test_force_reopens_a_stale_report_the_same_day(self):
        self.go(MONDAY, collect_fn=self.offline)
        result = self.go(MONDAY, collect_fn=self.offline, force=True)
        self.assertTrue(result.opened)

    def test_explanation_for_total_failure(self):
        result = self.go(MONDAY, collect_fn=self.offline)
        self.assertIn("avertissement", result.explanation)

    def test_every_failed_source_is_listed_with_its_exact_message(self):
        result = self.go(MONDAY, collect_fn=self.offline)
        self.assertEqual([p.source for p in result.problems], ["Faux 0", "Faux 1"])
        for problem in result.problems:
            self.assertEqual(problem.message, "TimeoutError: delai depasse")

    def test_partial_failure_still_opens_the_report_and_reports_the_problem(self):
        result = self.go(MONDAY, collect_fn=self.partial)
        self.assertFalse(result.all_failed)
        self.assertTrue(result.opened)
        html = self._html(result)
        self.assertIn("Certaines sources n&#39;ont pas pu etre collectees", html)
        self.assertIn("Faux 1", html)
        self.assertEqual([p.source for p in result.problems], ["Faux 1"])
        self.assertFalse(result.ok)
        self.assertIn("incompletes", result.explanation)

    def test_aggregation_failure_is_reported(self):
        def collect(config, today=None):
            run = self.collect(config, today)
            run.derive_error = "OperationalError: database is locked"
            return run

        result = self.go(MONDAY, collect_fn=collect)
        self.assertTrue(result.opened)
        self.assertEqual(result.problems[0].source, "Agregat box-office monde")
        self.assertIn("database is locked", result.problems[0].message)

    def test_browser_failure_becomes_a_problem(self):
        self.browser_works = False
        result = self.go(MONDAY)
        self.assertEqual([p.source for p in result.problems], ["Navigateur"])
        self.assertIn("infoshebdo-2026-10-05.html", result.problems[0].message)
        self.assertIn("n'a pas pu etre ouvert", result.explanation)

    def test_clean_run_has_no_problem_and_no_warning(self):
        result = self.go(MONDAY)
        self.assertEqual(result.problems, [])
        self.assertTrue(result.ok)
        self.assertNotIn("Attention", self._html(result))

    def test_failure_on_a_day_already_collected_is_not_reported_twice(self):
        # Une collecte partiellement reussie compte pour la journee : le
        # declenchement suivant ne relance rien et ne rouvre donc pas de fenetre.
        today = date.today()
        first = self.go(today, collect_fn=self.partial)
        second = self.go(today)
        self.assertEqual(len(first.problems), 1)
        self.assertEqual(second.problems, [])


class TestCollectedOn(AutoCase):
    def test_no_run_means_not_collected(self):
        with db.session() as conn:
            self.assertFalse(db.collected_on(conn, date.today()))

    def test_successful_run_counts_for_today_only(self):
        self.collect(self.config)
        with db.session() as conn:
            self.assertTrue(db.collected_on(conn, date.today()))
            self.assertFalse(db.collected_on(conn, date(2020, 1, 1)))

    def test_unfinished_run_does_not_count(self):
        with db.session() as conn:
            run_id = db.start_run(conn, "collect")
            db.log_collector(conn, run_id, "faux", "Faux", "ok", 1, 1)
            self.assertFalse(db.collected_on(conn, date.today()))

    def test_run_with_only_errors_does_not_count(self):
        self.collect(self.config, statuses=("erreur",))
        with db.session() as conn:
            self.assertFalse(db.collected_on(conn, date.today()))


if __name__ == "__main__":
    unittest.main()
