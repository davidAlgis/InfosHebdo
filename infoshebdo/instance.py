"""Instance unique de l'application residente.

L'application demarre toute seule avec la session ; si l'utilisateur la relance
a la main (double-clic sur l'executable), il ne doit pas en naitre une seconde :
deux icones, deux collectes qui se disputent la base. La seconde instance
previent la premiere (qui affiche sa fenetre) puis s'arrete.

Le dialogue passe par un port local (127.0.0.1) : aucune dependance, et le meme
mecanisme sous Windows, Linux et macOS. Le serveur repond par une signature
connue : si un tout autre programme occupe le port, on ne le prend pas pour une
instance d'InfosHebdo (et on demarre quand meme, sans exclusivite).
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_PORT = 47891
SIGNATURE = b"infoshebdo\n"
SHOW = b"show\n"


class SingleInstance:
    def __init__(self, on_show: Callable[[], None], port: int = DEFAULT_PORT) -> None:
        self.on_show = on_show
        self.port = port
        self._server: socket.socket | None = None

    # ------------------------------------------------------------------ #
    def acquire(self) -> bool:
        """Devient l'instance principale. False si le port est deja pris."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Pas de SO_REUSEADDR : sous Windows il autoriserait deux serveurs
            # sur le meme port, ce qui annule tout l'interet.
            server.bind(("127.0.0.1", self.port))
            server.listen(4)
        except OSError:
            server.close()
            return False
        self.port = server.getsockname()[1]
        self._server = server
        threading.Thread(target=self._serve, name="infoshebdo-instance", daemon=True).start()
        return True

    def _serve(self) -> None:
        server = self._server
        while server is not None:
            try:
                connection, _ = server.accept()
            except OSError:
                return                      # socket fermee : fin normale
            with connection:
                try:
                    connection.settimeout(2)
                    request = connection.recv(32)
                    connection.sendall(SIGNATURE)
                except OSError:
                    continue
            if request == SHOW:
                try:
                    self.on_show()
                except Exception as exc:  # noqa: BLE001 - ne jamais tuer le serveur
                    log.warning("reveil de l'instance : %s", exc)

    # ------------------------------------------------------------------ #
    def signal_existing(self) -> bool:
        """Demande a l'instance deja lancee de se montrer. False s'il n'y en a pas."""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.5) as client:
                client.settimeout(2)
                client.sendall(SHOW)
                return client.recv(len(SIGNATURE)) == SIGNATURE
        except OSError:
            return False

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
