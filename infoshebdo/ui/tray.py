"""Icone dans la zone de notification, pres de l'horloge.

pystray fait tourner sa propre boucle d'evenements, distincte de celle de Tk.
Un clic sur le menu s'execute donc sur le fil de pystray : appeler un widget Tk
depuis la ne fonctionne pas de facon fiable et provoque des blocages
aleatoires. On depose donc les actions dans une file, que la fenetre vide sur
son propre fil — le meme mecanisme que pour les traitements en tache de fond.

L'icone est facultative : si pystray n'est pas installe, ou si le systeme n'a
pas de zone de notification, l'application fonctionne normalement et la fenetre
se comporte comme une fenetre ordinaire.
"""
from __future__ import annotations

import logging
import queue
from typing import Callable

log = logging.getLogger(__name__)

TOOLTIP = "InfosHebdo - veille cinema et jeu video"


def available() -> bool:
    """True si l'icone de notification peut etre creee."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


class TrayIcon:
    """Enveloppe de pystray, sans dependance a Tk."""

    def __init__(self, commands: queue.Queue) -> None:
        self.commands = commands
        self._icon = None

    # ------------------------------------------------------------------ #
    def _post(self, action: str) -> Callable:
        """Fabrique un gestionnaire de menu qui ne fait que poster l'action."""

        def handler(_icon=None, _item=None) -> None:
            self.commands.put(action)

        return handler

    def start(self) -> bool:
        """Cree et affiche l'icone. Renvoie False si ce n'est pas possible."""
        if not available():
            log.info("pystray indisponible : pas d'icone de notification")
            return False

        import pystray

        from .. import assets

        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir InfosHebdo", self._post("show"), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Collecter maintenant", self._post("collect")),
            pystray.MenuItem("Generer et ouvrir le rapport", self._post("report")),
            pystray.MenuItem("Ouvrir le dernier rapport", self._post("open_report")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", self._post("quit")),
        )

        try:
            self._icon = pystray.Icon(
                "infoshebdo",
                icon=assets.tray_image(64),
                title=TOOLTIP,
                menu=menu,
            )
            # `run_detached` laisse la boucle Tk maitresse du fil principal.
            self._icon.run_detached()
        except Exception as exc:  # noqa: BLE001 - depend du systeme
            log.warning("icone de notification indisponible : %s", exc)
            self._icon = None
            return False
        return True

    @property
    def running(self) -> bool:
        return self._icon is not None

    def notify(self, message: str, title: str = "InfosHebdo") -> None:
        """Bulle d'information. Silencieuse si le systeme ne la gere pas."""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001
            log.debug("notification impossible : %s", exc)

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("arret de l'icone : %s", exc)
        finally:
            self._icon = None
