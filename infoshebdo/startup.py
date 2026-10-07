"""Lancement au demarrage de Windows.

On inscrit une valeur dans la cle « Run » de l'utilisateur courant :

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

Ce choix plutot qu'un raccourci dans le dossier Demarrage, ou qu'une tache
planifiee « a l'ouverture de session » :

* `winreg` est dans la bibliotheque standard : aucune dependance, et pas
  besoin de COM pour fabriquer un .lnk ;
* la cle est propre a l'utilisateur, donc aucune elevation de privileges ;
* l'entree est visible et desactivable par l'utilisateur dans le Gestionnaire
  des taches, onglet « Demarrage ». Une automatisation qu'on ne peut pas
  retrouver et couper est une mauvaise automatisation.

L'interface est lancee avec `--minimized` : au demarrage de la session, elle
va directement dans la zone de notification au lieu de s'ouvrir devant
l'utilisateur.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "InfosHebdo"


class StartupError(RuntimeError):
    """Erreur d'inscription au demarrage, avec un message affichable."""


@dataclass
class StartupState:
    enabled: bool
    command: str = ""
    matches_current: bool = True

    @property
    def summary(self) -> str:
        if not self.enabled:
            return "non actif"
        if not self.matches_current:
            return "actif, mais pointe vers un autre emplacement"
        return "actif"


def is_supported() -> bool:
    return os.name == "nt"


def _require_windows() -> None:
    if not is_supported():
        raise StartupError(
            "Le lancement au demarrage passe par le registre Windows. Sur un "
            "autre systeme, ajouter l'interface aux applications de session."
        )


def startup_command() -> str:
    """Ligne de commande inscrite dans le registre."""
    prefix = paths.command_prefix()
    parts = [f'"{prefix[0]}"']
    parts.extend(f'"{p}"' if " " in p else p for p in prefix[1:])
    parts.extend(["ui", "--minimized"])
    return " ".join(parts)


def status() -> StartupState:
    if not is_supported():
        return StartupState(enabled=False)

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return StartupState(enabled=False)
    except OSError as exc:  # pragma: no cover - registre inaccessible
        raise StartupError(f"lecture du registre impossible : {exc}") from exc

    return StartupState(
        enabled=True,
        command=str(value),
        matches_current=str(value) == startup_command(),
    )


def enable() -> str:
    """Inscrit l'interface au demarrage. Renvoie la commande inscrite."""
    _require_windows()
    import winreg

    command = startup_command()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        raise StartupError(f"ecriture dans le registre refusee : {exc}") from exc
    return command


def disable() -> bool:
    """Retire l'entree. Renvoie False si elle n'existait pas."""
    _require_windows()
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StartupError(f"suppression dans le registre refusee : {exc}") from exc
    return True


def describe() -> list[str]:
    lines = [f"Commande : {startup_command()}"]
    if not is_supported():
        lines.append("Registre Windows indisponible sur ce systeme.")
        return lines
    state = status()
    lines.append(f"Etat     : {state.summary}")
    if state.enabled and not state.matches_current:
        lines.append(f"Inscrit  : {state.command}")
    lines.append(f"Cle      : HKCU\\{RUN_KEY}\\{VALUE_NAME}")
    return lines
