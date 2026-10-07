"""Adresses des pages qui publient les chiffres du rapport.

Chaque classement du rapport renvoie vers sa source : on peut verifier un
chiffre en un clic, a l'endroit meme ou il est publie. Quand la semaine du
classement est connue, le lien pointe sur cette semaine-la (et pas sur la page
d'accueil de la source, qui aurait change depuis) ; sinon sur la page generale.

Les gabarits d'adresses des collecteurs sont repris tels quels : le lien du
rapport et ce que la collecte a reellement telecharge ne peuvent pas diverger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import weeks
from .collectors.allocine import WEEK_URL as ALLOCINE_WEEK_URL
from .collectors.base import market_label
from .collectors.boxofficemojo import BASE_URL as MOJO_WEEK_URL

ALLOCINE_HOME = "https://www.allocine.fr/boxoffice/france/"
MOJO_HOME = "https://www.boxofficemojo.com/weekend/"
STEAM_TOPSELLERS_URL = "https://store.steampowered.com/charts/topselling/{region}"
STEAM_MOST_PLAYED_URL = "https://store.steampowered.com/charts/mostplayed"
STEAMSPY_URL = "https://steamspy.com/"


@dataclass(frozen=True)
class Link:
    label: str
    url: str


def _with_area(url: str, area: str) -> str:
    """La zone par defaut de Mojo (Amerique du Nord) n'a pas de parametre."""
    return f"{url}?area={area}" if area else url


def allocine_week(period_start: str | None) -> Link:
    """Page Allocine de la semaine (mercredi -> mardi), ou la page generale."""
    if period_start:
        return Link(
            f"Allocine - box-office France, semaine du {_french_day(period_start)}",
            ALLOCINE_WEEK_URL.format(day=period_start),
        )
    return Link("Allocine - box-office France", ALLOCINE_HOME)


def mojo_week(period_start: str | None, area: str, label: str) -> Link:
    """Page Box Office Mojo du week-end commencant a `period_start` (un vendredi).

    On part de la date, toujours stockee, plutot que du libelle « 2026W33 » que
    Mojo lui-meme employait : le numero de semaine se recalcule avec le meme
    calendrier que la collecte. Sans date lisible : page generale de la zone.
    """
    try:
        year, week = weeks.mojo_week_of(date.fromisoformat(period_start or ""))
    except ValueError:
        return Link(label, _with_area(MOJO_HOME, area))
    url = MOJO_WEEK_URL.format(year=year, week=week)
    return Link(f"{label} ({year}W{week:02d})", _with_area(url, area))


def steam_topsellers(market: str) -> Link:
    """Classement Steam d'un pays ; `WW` est le classement mondial."""
    region = "global" if market == "WW" else market
    return Link(
        f"Steam - meilleures ventes ({market_label(market)})",
        STEAM_TOPSELLERS_URL.format(region=region),
    )


def steam_most_played() -> Link:
    return Link("Steam - jeux les plus joues", STEAM_MOST_PLAYED_URL)


def steamspy() -> Link:
    return Link("SteamSpy", STEAMSPY_URL)


def _french_day(iso_day: str) -> str:
    year, month, day = iso_day.split("-")
    return f"{int(day):02d}/{int(month):02d}/{year}"
