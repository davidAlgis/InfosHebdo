"""Ouverture du rapport dans le navigateur par defaut.

Le rapport n'est plus envoye : il s'ouvre tout seul, une fois par semaine.
« Une fois par semaine » est memorise en base, par semaine ISO, plutot que
deduit de la date du fichier ou de l'heure de la tache : c'est ce qui permet
a une tache lancee tous les jours (et a l'ouverture de session) de n'ouvrir le
rapport que la premiere fois de la semaine ou le poste est allume — le lundi
s'il l'est, sinon le mardi, et ainsi de suite.
"""
from __future__ import annotations

import logging
import sqlite3
import webbrowser
from datetime import date
from pathlib import Path

from . import db, paths

log = logging.getLogger(__name__)

STATE_KEY = "last_report_week"
STALE_KEY = "last_stale_report_day"
REPORT_GLOB = "infoshebdo-*.html"


def week_key(day: date) -> str:
    """Semaine ISO, annee comprise : '2026-W41'.

    L'annee ISO peut differer de l'annee civile autour du 1er janvier, d'ou
    `isocalendar()` plutot qu'un `strftime`.
    """
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def latest_report() -> Path | None:
    """Rapport HTML le plus recent, ou None."""
    if not paths.REPORTS_DIR.exists():
        return None
    reports = sorted(paths.REPORTS_DIR.glob(REPORT_GLOB))
    return reports[-1] if reports else None


def open_report(path: Path) -> bool:
    """Ouvre un rapport dans le navigateur par defaut.

    Un echec n'est jamais fatal : le rapport reste sur le disque et le chemin
    est journalise.
    """
    if not path.exists():
        log.warning("rapport introuvable : %s", path)
        return False
    try:
        # `as_uri()` gere les espaces et les accents des chemins Windows, que
        # `webbrowser.open` ne quote pas de lui-meme.
        opened = webbrowser.open(path.resolve().as_uri())
    except Exception as exc:  # noqa: BLE001 - depend du systeme
        log.warning("ouverture du navigateur impossible : %s", exc)
        return False
    if opened:
        log.info("rapport ouvert dans le navigateur : %s", path)
    else:
        log.warning("aucun navigateur disponible pour ouvrir %s", path)
    return opened


def already_shown(conn: sqlite3.Connection, today: date) -> bool:
    """True si le rapport de la semaine de `today` a deja ete ouvert."""
    return db.get_state(conn, STATE_KEY) == week_key(today)


def mark_shown(conn: sqlite3.Connection, today: date) -> None:
    db.set_state(conn, STATE_KEY, week_key(today))


def stale_shown_on(conn: sqlite3.Connection, today: date) -> bool:
    """True si un rapport « de secours » (donnees anciennes) a deja ete ouvert ce jour.

    Apres un echec de collecte, le rapport s'ouvre avec un avertissement sans
    consommer la semaine. Cette marque, elle, evite de le rouvrir a chaque
    nouvelle tentative de la journee.
    """
    return db.get_state(conn, STALE_KEY) == today.isoformat()


def mark_stale_shown(conn: sqlite3.Connection, today: date) -> None:
    db.set_state(conn, STALE_KEY, today.isoformat())
