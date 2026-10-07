"""Tests de l'instance unique.

L'application demarre avec la session : relancee a la main, elle ne doit pas
en creer une seconde (deux icones, deux collectes dans la meme base). Ces tests
utilisent de vrais sockets locaux, sur un port choisi par le systeme.
"""
from __future__ import annotations

import logging
import socket
import threading
import unittest

from infoshebdo.instance import SingleInstance


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.shown = threading.Event()
        self.first = SingleInstance(on_show=self.shown.set, port=0)
        self.addCleanup(self.first.close)
        self.assertTrue(self.first.acquire())

    def test_a_second_instance_cannot_acquire(self):
        second = SingleInstance(on_show=lambda: None, port=self.first.port)
        self.addCleanup(second.close)
        self.assertFalse(second.acquire())

    def test_the_second_instance_wakes_the_first(self):
        second = SingleInstance(on_show=lambda: None, port=self.first.port)
        self.assertTrue(second.signal_existing())
        self.assertTrue(self.shown.wait(timeout=3))

    def test_each_relaunch_wakes_it_again(self):
        second = SingleInstance(on_show=lambda: None, port=self.first.port)
        for _ in range(3):
            self.shown.clear()
            self.assertTrue(second.signal_existing())
            self.assertTrue(self.shown.wait(timeout=3))

    def test_nothing_running_means_no_one_to_signal(self):
        free = SingleInstance(on_show=lambda: None, port=0)
        free.acquire()
        port = free.port
        free.close()                                   # le port est libre, plus personne
        self.assertFalse(SingleInstance(on_show=lambda: None, port=port).signal_existing())

    def test_a_failing_callback_does_not_kill_the_listener(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("fenetre indisponible")

        server = SingleInstance(on_show=flaky, port=0)
        self.addCleanup(server.close)
        server.acquire()
        client = SingleInstance(on_show=lambda: None, port=server.port)
        self.assertTrue(client.signal_existing())
        self.assertTrue(client.signal_existing())      # le second appel passe
        for _ in range(50):
            if len(calls) == 2:
                break
            threading.Event().wait(0.05)
        self.assertEqual(len(calls), 2)

    def test_a_foreign_program_on_the_port_is_not_mistaken_for_an_instance(self):
        # Un autre programme occupe le port : ne pas conclure « deja lance »,
        # sinon l'application ne demarrerait jamais.
        stranger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(stranger.close)
        stranger.bind(("127.0.0.1", 0))
        stranger.listen(1)

        def answer():
            connection, _ = stranger.accept()
            with connection:
                connection.recv(32)
                connection.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")

        threading.Thread(target=answer, daemon=True).start()
        client = SingleInstance(on_show=lambda: None, port=stranger.getsockname()[1])
        self.assertFalse(client.signal_existing())

    def test_close_frees_the_port(self):
        port = self.first.port
        self.first.close()
        again = SingleInstance(on_show=lambda: None, port=port)
        self.addCleanup(again.close)
        self.assertTrue(again.acquire())


if __name__ == "__main__":
    unittest.main()
