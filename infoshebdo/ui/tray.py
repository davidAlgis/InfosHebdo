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

    def __init__(
        self, commands: queue.Queue, autostart_checked: Callable[[], bool] | None = None
    ) -> None:
        self.commands = commands
        # Fournie par l'application : lit l'etat reel (registre) a chaque
        # ouverture du menu. None : pas de case « lancer au demarrage ».
        self.autostart_checked = autostart_checked
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

        items = [
            pystray.MenuItem("Ouvrir InfosHebdo", self._post("show"), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Rechercher les donnees", self._post("search")),
            pystray.MenuItem("Ouvrir le rapport", self._post("report")),
            pystray.Menu.SEPARATOR,
        ]
        if self.autostart_checked is not None:
            items.append(
                pystray.MenuItem(
                    "Lancer au demarrage de Windows",
                    self._post("toggle_autostart"),
                    checked=lambda _item: bool(self.autostart_checked()),
                )
            )
        items.append(pystray.MenuItem("Quitter", self._post("quit")))
        menu = pystray.Menu(*items)

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

    def refresh_menu(self) -> None:
        """Releit les cases a cocher (apres un changement fait depuis le menu)."""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception as exc:  # noqa: BLE001
                log.debug("menu de l'icone non actualise : %s", exc)

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
