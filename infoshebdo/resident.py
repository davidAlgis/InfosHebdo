"""Logique de l'application residente, sans aucune dependance a Tk.

L'application tourne en permanence dans la zone de notification. Elle remplace
la tache planifiee : rien n'est a lancer ni a installer a la main.

* au premier lancement, elle s'inscrit au demarrage de la session ;
* elle verifie au demarrage, puis toutes les 30 minutes, s'il y a quelque chose
  a faire (voir `auto.run`) : collecter si ce n'est pas deja fait aujourd'hui,
  ouvrir le rapport si la semaine n'a pas encore le sien. Une session laissee
  ouverte d'un dimanche au lundi ouvre ainsi le rapport le lundi, sans relance ;
* deux actions manuelles : rechercher les donnees, ouvrir le rapport.

Tout ce qui se decide est ici, pour etre teste sans fenetre. `ui/app.py` ne fait
que l'afficher.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from . import auto, db, pipeline, report as report_module, startup, viewer
from .config import Config

log = logging.getLogger(__name__)

# Delai entre deux verifications « y a-t-il quelque chose a faire ? ».
TICK_SECONDS = 30 * 60

# Memoire (en base) du refus de l'utilisateur : une fois le lancement au
# demarrage coupe depuis l'icone, on ne le reinscrit plus jamais de force.
AUTOSTART_OFF_KEY = "autostart_disabled"

# Pour les essais et la fabrication de l'executable : ne rien inscrire dans le
# registre de la machine.
NO_AUTOSTART_ENV = "INFOSHEBDO_NO_AUTOSTART"


# --------------------------------------------------------------------------- #
# Lancement au demarrage
# --------------------------------------------------------------------------- #
def ensure_autostart() -> str:
    """Inscrit le lancement a la session, une fois, sauf refus de l'utilisateur.

    Renvoie ce qui s'est passe : 'enabled', 'updated' (l'application a change
    d'emplacement), 'already', 'disabled-by-user', 'disabled-by-env',
    'unsupported' ou 'error: ...'. Ne leve jamais : un registre inaccessible ne
    doit pas empecher l'application de demarrer.
    """
    if os.environ.get(NO_AUTOSTART_ENV) == "1":
        return "disabled-by-env"
    if not startup.is_supported():
        return "unsupported"
    try:
        with db.session() as conn:
            if db.get_state(conn, AUTOSTART_OFF_KEY) == "1":
                return "disabled-by-user"
        state = startup.status()
        if state.enabled and state.matches_current:
            return "already"
        startup.enable()
        return "updated" if state.enabled else "enabled"
    except Exception as exc:  # noqa: BLE001 - jamais bloquant
        log.warning("lancement au demarrage non inscrit : %s", exc)
        return f"error: {exc}"


def autostart_enabled() -> bool:
    """True si le lancement au demarrage est actuellement inscrit."""
    try:
        return startup.status().enabled
    except Exception:  # noqa: BLE001
        return False


def set_autostart(enabled: bool) -> None:
    """Choix de l'utilisateur (menu de l'icone) : memorise puis applique."""
    with db.session() as conn:
        db.set_state(conn, AUTOSTART_OFF_KEY, "" if enabled else "1")
    if enabled:
        startup.enable()
    else:
        startup.disable()


# --------------------------------------------------------------------------- #
# Fenetre d'erreur : une fois par jour pour les verifications automatiques
# --------------------------------------------------------------------------- #
class DailyNotice:
    """Autorise une notification par jour.

    Hors ligne, la verification automatique echoue toutes les 30 minutes :
    ouvrir une fenetre a chaque fois serait insupportable. La premiere du jour
    suffit ; les actions manuelles, elles, s'affichent toujours.
    """

    def __init__(self) -> None:
        self._day: date | None = None

    def allow(self, today: date) -> bool:
        if self._day == today:
            return False
        self._day = today
        return True


# --------------------------------------------------------------------------- #
# Actions manuelles
# --------------------------------------------------------------------------- #
@dataclass
class SearchResult:
    message: str = ""
    problems: list[auto.Problem] = field(default_factory=list)
    all_failed: bool = False

    @property
    def explanation(self) -> str:
        if self.all_failed:
            return (
                "Aucune source n'a repondu : verifiez la connexion Internet. Les "
                "chiffres de la base datent de la derniere recherche reussie."
            )
        return (
            "Certaines sources n'ont pas repondu. Les donnees des autres sont "
            "enregistrees ; le rapport signale les sections incompletes."
        )


def search_data(
    config: Config,
    today: date | None = None,
    collect_fn: Callable[..., "pipeline.CollectRun"] | None = None,
) -> SearchResult:
    """« Rechercher les donnees » : une collecte complete, a la demande."""
    collect_fn = collect_fn or pipeline.collect
    run = collect_fn(config, today=today or date.today())
    problems = auto.problems_from(run)
    message = f"{run.total_rows} observation(s), {run.total_news} article(s)"
    if problems:
        message += f", {len(problems)} probleme(s)"
    return SearchResult(message=message, problems=problems, all_failed=run.all_failed)


@dataclass
class OpenResult:
    path: Path | None = None
    opened: bool = False
    has_data: bool = False

    @property
    def message(self) -> str:
        if self.opened:
            return "Rapport ouvert dans le navigateur."
        return (
            f"Rapport genere ({self.path.name}), mais le navigateur n'a pas pu "
            "l'ouvrir." if self.path else "Rapport non genere."
        )


def open_report_now(
    config: Config,
    today: date | None = None,
    open_fn: Callable[[Path], bool] | None = None,
) -> OpenResult:
    """« Ouvrir le rapport » : le reconstruit depuis la base, puis l'ouvre.

    Toujours reconstruit, jamais relu depuis un ancien fichier : il reflete les
    dernieres donnees, et il n'y a pas de cas « aucun rapport pour l'instant ».
    L'ouvrir a la main compte comme l'avoir vu : le rapport automatique de la
    semaine ne reviendra pas le rouvrir derriere.
    """
    today = today or date.today()
    open_fn = open_fn or viewer.open_report
    report, html, text = report_module.build(config, today)
    html_path, _ = report_module.save(html, text, today)
    result = OpenResult(path=html_path, has_data=report.has_data)
    if open_fn(html_path):
        result.opened = True
        with db.session() as conn:
            viewer.mark_shown(conn, today)
    return result


# --------------------------------------------------------------------------- #
# Etat affiche dans la fenetre
# --------------------------------------------------------------------------- #
def status_lines(today: date | None = None) -> list[str]:
    """Deux lignes : derniere recherche, rapport de la semaine."""
    today = today or date.today()
    with db.session() as conn:
        last = db.last_successful_collect(conn)
        shown = viewer.already_shown(conn, today)
    return [
        "Derniere recherche des donnees : "
        + (_format_when(last, today) if last else "jamais"),
        "Rapport de cette semaine : "
        + ("deja ouvert" if shown else "ouverture automatique prevue"),
    ]


def _format_when(moment: datetime, today: date) -> str:
    if moment.date() == today:
        return f"aujourd'hui a {moment:%H:%M}"
    return f"le {moment:%d/%m/%Y} a {moment:%H:%M}"
