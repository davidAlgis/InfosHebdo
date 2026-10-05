"""Tests : une collecte hors ligne doit apparaitre comme une panne, pas comme « vide ».

Les collecteurs avalent les echecs reseau dans de simples notes, car une
semaine manquante est normale (le box-office France et Japon arrive avec des
semaines de retard). Resultat observe : sans reseau, TOUTES les sources se
declaraient « vide » et aucune fenetre d'erreur ne s'ouvrait. Le pipeline juge
donc d'apres les requetes reellement faites : une source dont aucune requete
n'a abouti est en erreur ; une source jointe mais sans donnee reste « vide ».
"""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from infoshebdo import db, pipeline
from infoshebdo.collectors.base import Collector, CollectorResult
from infoshebdo.config import DEFAULTS, Config
from infoshebdo.http import Client, FetchError


def reply(status: int = 200, text: str = "ok") -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.encoding = "utf-8"
    resp.url = "https://exemple.test/x"
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(str(status))
    return resp


class Fetcher(Collector):
    """Source de test : telecharge des pages, avale les echecs comme les vraies."""

    domain = "box_office"

    def __init__(self, client, config, name: str, urls: list[str]) -> None:
        super().__init__(client, config)
        self.name = name
        self.label = f"Source {name}"
        self.urls = urls

    def run(self, today):
        result = CollectorResult()
        for url in self.urls:
            try:
                self.client.get_text(url)
            except FetchError as exc:
                result.notes.append(str(exc))
        return result


class LocalSource(Collector):
    """Source locale (import CSV) : aucune requete reseau."""

    domain = "games"
    name = "manual_import"
    label = "Import manuel"

    def run(self, today):
        return CollectorResult(notes=["Aucun CSV a importer."])


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        env = mock.patch.dict(os.environ, {"INFOSHEBDO_DB": str(Path(self._dir.name) / "t.sqlite3")})
        env.start()
        self.addCleanup(env.stop)
        sleep = mock.patch("infoshebdo.http.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        db.init()

        self.config = Config(raw=copy.deepcopy(DEFAULTS))
        self.session = mock.MagicMock()

    def collect(self, collectors_factory) -> pipeline.CollectRun:
        def make_client(delay=0):
            client = Client(delay=0, use_cache=False)
            client.session = self.session
            return client

        with mock.patch.object(pipeline, "Client", make_client), \
             mock.patch.object(
                 pipeline, "registry_instances",
                 lambda client, config: collectors_factory(client, config),
             ):
            return pipeline.collect(self.config)

    @staticmethod
    def by_name(run: pipeline.CollectRun) -> dict[str, pipeline.CollectorOutcome]:
        return {o.name: o for o in run.outcomes}


class TestOffline(PipelineCase):
    def test_every_source_without_a_single_answer_is_an_error(self):
        self.session.get.side_effect = requests.ConnectionError("hors ligne")
        run = self.collect(lambda c, cfg: [
            Fetcher(c, cfg, "a", ["https://a.test/1", "https://a.test/2"]),
            Fetcher(c, cfg, "b", ["https://b.test/1"]),
        ])
        outcomes = self.by_name(run)
        self.assertEqual(outcomes["a"].status, "erreur")
        self.assertEqual(outcomes["b"].status, "erreur")
        self.assertTrue(run.all_failed)
        self.assertEqual(len(run.failed), 2)

    def test_the_error_explains_the_cause_in_plain_words(self):
        self.session.get.side_effect = requests.ConnectionError("hors ligne")
        run = self.collect(lambda c, cfg: [Fetcher(c, cfg, "a", ["https://a.test/1"])])
        message = self.by_name(run)["a"].error
        self.assertIn("connexion impossible", message)
        self.assertIn("ConnectionError", message)

    def test_extra_failures_are_counted_in_the_message(self):
        self.session.get.side_effect = requests.ConnectionError("hors ligne")
        urls = [f"https://a.test/{n}" for n in range(4)]
        run = self.collect(lambda c, cfg: [Fetcher(c, cfg, "a", urls)])
        self.assertIn("(+3 autre(s) requete(s) en echec)", self.by_name(run)["a"].error)

    def test_error_is_stored_in_the_collector_log(self):
        self.session.get.side_effect = requests.ConnectionError("hors ligne")
        self.collect(lambda c, cfg: [Fetcher(c, cfg, "a", ["https://a.test/1"])])
        with db.session() as conn:
            rows = db.last_run_log(conn, "collect")
        row = next(r for r in rows if r["collector"] == "a")
        self.assertEqual(row["status"], "erreur")
        self.assertIn("connexion impossible", row["message"])

    def test_a_local_source_does_not_hide_a_total_failure(self):
        # L'import CSV n'utilise pas le reseau : il ne doit pas empecher de
        # constater que toutes les sources reseau sont tombees.
        self.session.get.side_effect = requests.ConnectionError("hors ligne")
        run = self.collect(lambda c, cfg: [
            Fetcher(c, cfg, "a", ["https://a.test/1"]),
            LocalSource(c, cfg),
        ])
        outcomes = self.by_name(run)
        self.assertEqual(outcomes["manual_import"].status, "vide")
        self.assertEqual(outcomes["manual_import"].requests, 0)
        self.assertTrue(run.all_failed)


class TestReachableButEmpty(PipelineCase):
    def test_a_source_that_answers_without_data_stays_empty(self):
        self.session.get.return_value = reply(text="<html></html>")
        run = self.collect(lambda c, cfg: [Fetcher(c, cfg, "a", ["https://a.test/1"])])
        self.assertEqual(self.by_name(run)["a"].status, "vide")
        self.assertFalse(run.all_failed)

    def test_some_answers_among_failures_is_not_an_error(self):
        # Semaines passees introuvables : normal pour le box-office tardif.
        self.session.get.side_effect = [reply(text="semaine ok"), reply(404), reply(404)]
        run = self.collect(lambda c, cfg: [
            Fetcher(c, cfg, "a", [f"https://a.test/{n}" for n in range(3)])
        ])
        outcome = self.by_name(run)["a"]
        self.assertEqual(outcome.status, "vide")
        self.assertEqual(outcome.error, "")
        self.assertFalse(run.all_failed)

    def test_every_page_missing_is_an_error(self):
        # Toutes les adresses en 404 : le site a change, il faut le dire.
        self.session.get.return_value = reply(404)
        run = self.collect(lambda c, cfg: [
            Fetcher(c, cfg, "a", [f"https://a.test/{n}" for n in range(3)])
        ])
        self.assertEqual(self.by_name(run)["a"].status, "erreur")

    def test_one_source_down_among_working_ones_is_partial(self):
        def get(url, **kwargs):
            if "mort.test" in url:
                raise requests.ConnectionError("serveur en panne")
            return reply(text="ok")

        self.session.get.side_effect = get
        run = self.collect(lambda c, cfg: [
            Fetcher(c, cfg, "vivant", ["https://vivant.test/1"]),
            Fetcher(c, cfg, "mort", ["https://mort.test/1"]),
        ])
        outcomes = self.by_name(run)
        self.assertEqual(outcomes["vivant"].status, "vide")
        self.assertEqual(outcomes["mort"].status, "erreur")
        self.assertFalse(run.all_failed)            # partiel, pas total
        self.assertEqual([o.name for o in run.failed], ["mort"])


class TestClientCounters(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("infoshebdo.http.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Client(delay=0, use_cache=False)
        self.client.session = mock.MagicMock()

    def test_success_is_counted(self):
        self.client.session.get.return_value = reply()
        self.client.get_text("https://a.test/1")
        self.assertEqual((self.client.ok, self.client.failures), (1, []))

    def test_failure_is_recorded_with_its_message(self):
        self.client.session.get.return_value = reply(404)
        with self.assertRaises(FetchError):
            self.client.get_text("https://a.test/1")
        self.assertEqual(self.client.ok, 0)
        self.assertEqual(len(self.client.failures), 1)
        self.assertIn("404", self.client.failures[0])

    def test_invalid_json_is_a_failure_not_a_success(self):
        self.client.session.get.return_value = reply(text="<html>pas du json</html>")
        with self.assertRaises(FetchError):
            self.client.get_json("https://a.test/api")
        self.assertEqual(self.client.ok, 0)
        self.assertIn("non-JSON", self.client.failures[0])

    def test_timeout_is_described_in_plain_words(self):
        self.client.session.get.side_effect = requests.Timeout("trop lent")
        with self.assertRaises(FetchError) as ctx:
            self.client.get_text("https://a.test/1")
        self.assertIn("delai depasse", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
