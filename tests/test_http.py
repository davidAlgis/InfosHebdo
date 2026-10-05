"""Tests du client HTTP : reessais et abandon d'un hote injoignable.

Aucun acces reseau : la session `requests` est remplacee par un faux.
"""
from __future__ import annotations

import unittest
from unittest import mock

import requests

from infoshebdo import http
from infoshebdo.http import Client, FetchError


def make_client() -> tuple[Client, mock.MagicMock]:
    client = Client(delay=0, use_cache=False)
    client.session = mock.MagicMock()
    return client, client.session


def response(status: int = 200, text: str = "ok") -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.encoding = "utf-8"
    resp.url = "https://exemple.test/x"
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return resp


class TestClient(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(http.time, "sleep")   # pas de vraie pause
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_successful_fetch(self):
        client, session = make_client()
        session.get.return_value = response(text="bonjour")
        self.assertEqual(client.get_text("https://exemple.test/a"), "bonjour")

    def test_transient_failure_is_retried(self):
        client, session = make_client()
        session.get.side_effect = [requests.ConnectionError("coupure"), response(text="ok")]
        self.assertEqual(client.get_text("https://exemple.test/a"), "ok")
        self.assertEqual(session.get.call_count, 2)

    def test_gives_up_after_max_attempts(self):
        client, session = make_client()
        session.get.side_effect = requests.ConnectionError("hors ligne")
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/a")
        self.assertEqual(session.get.call_count, http.MAX_ATTEMPTS)


class TestUnreachableHost(unittest.TestCase):
    """Hors ligne, il ne faut pas retenter chaque page de chaque semaine."""

    def setUp(self):
        patcher = mock.patch.object(http.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dead_host_is_abandoned_for_the_rest_of_the_collection(self):
        client, session = make_client()
        session.get.side_effect = requests.ConnectionError("hors ligne")
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/semaine-1")
        calls_after_first = session.get.call_count

        for week in range(2, 10):
            with self.assertRaises(FetchError) as ctx:
                client.get_text(f"https://exemple.test/semaine-{week}")
            self.assertIn("abandonne", str(ctx.exception))
        self.assertEqual(session.get.call_count, calls_after_first)   # rien de plus

    def test_timeouts_count_as_unreachable(self):
        client, session = make_client()
        session.get.side_effect = requests.Timeout("trop lent")
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/a")
        before = session.get.call_count
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/b")
        self.assertEqual(session.get.call_count, before)

    def test_other_hosts_are_not_affected(self):
        client, session = make_client()
        session.get.side_effect = [requests.ConnectionError("x")] * http.MAX_ATTEMPTS + [
            response(text="autre hote")
        ]
        with self.assertRaises(FetchError):
            client.get_text("https://mort.test/a")
        self.assertEqual(client.get_text("https://vivant.test/a"), "autre hote")

    def test_http_errors_do_not_condemn_the_host(self):
        # Un 503 ou un 404 sur une page ne dit rien des autres pages du site.
        client, session = make_client()
        session.get.return_value = response(status=503)
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/a")
        session.get.return_value = response(text="la page suivante marche")
        self.assertEqual(client.get_text("https://exemple.test/b"), "la page suivante marche")

    def test_offline_machine_is_detected_after_three_dead_servers(self):
        client, session = make_client()
        session.get.side_effect = requests.ConnectionError("hors ligne")
        for host in ("a.test", "b.test", "c.test"):
            with self.assertRaises(FetchError):
                client.get_text(f"https://{host}/x")
        calls = session.get.call_count
        with self.assertRaises(FetchError) as ctx:
            client.get_text("https://d.test/x")           # 4e serveur : pas meme essaye
        self.assertEqual(session.get.call_count, calls)
        self.assertIn("hors ligne", str(ctx.exception))

    def test_working_internet_is_never_mistaken_for_offline(self):
        # Trois serveurs en panne ne prouvent rien si un autre a deja repondu.
        client, session = make_client()
        session.get.return_value = response(text="ok")
        client.get_text("https://vivant.test/x")
        session.get.side_effect = requests.ConnectionError("serveur en panne")
        for host in ("a.test", "b.test", "c.test"):
            with self.assertRaises(FetchError):
                client.get_text(f"https://{host}/x")
        session.get.side_effect = None
        session.get.return_value = response(text="toujours la")
        self.assertEqual(client.get_text("https://d.test/x"), "toujours la")

    def test_a_new_client_starts_with_a_clean_slate(self):
        client, session = make_client()
        session.get.side_effect = requests.ConnectionError("hors ligne")
        with self.assertRaises(FetchError):
            client.get_text("https://exemple.test/a")
        fresh, fresh_session = make_client()
        fresh_session.get.return_value = response(text="revenu")
        self.assertEqual(fresh.get_text("https://exemple.test/a"), "revenu")


if __name__ == "__main__":
    unittest.main()
