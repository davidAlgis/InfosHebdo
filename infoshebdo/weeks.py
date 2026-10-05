"""Calendriers de semaines.

Chaque source a sa propre definition de la semaine, et c'est la premiere
source d'erreurs silencieuses dans ce genre d'outil :

* Box Office Mojo    : week-end vendredi -> dimanche, numerote a partir du
                       premier vendredi de l'annee (2026W33 = 14/08/2026).
* Allocine (France)   : semaine cinema mercredi -> mardi.
* Steam              : semaine lundi -> dimanche, horodatage fourni par l'API.

On normalise tout en (period_start, period_end) dates, et on garde le
libelle d'origine dans period_label pour pouvoir remonter a la source.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

FRIDAY = 4
WEDNESDAY = 2


# --------------------------------------------------------------------------- #
# Box Office Mojo
# --------------------------------------------------------------------------- #
def first_friday(year: int) -> date:
    """Premier vendredi de l'annee : origine de la numerotation Mojo."""
    d = date(year, 1, 1)
    return d + timedelta(days=(FRIDAY - d.weekday()) % 7)


def mojo_week_to_dates(year: int, week: int) -> tuple[date, date]:
    """2026W33 -> (vendredi 14/08/2026, dimanche 16/08/2026)."""
    start = first_friday(year) + timedelta(weeks=week - 1)
    return start, start + timedelta(days=2)


def mojo_week_of(day: date) -> tuple[int, int]:
    """Semaine Mojo contenant (ou precedant) ce jour."""
    year = day.year
    origin = first_friday(year)
    if day < origin:
        year -= 1
        origin = first_friday(year)
    week = (day - origin).days // 7 + 1
    return year, week


def latest_complete_mojo_week(today: date) -> tuple[int, int]:
    """Dernier week-end termine.

    Un week-end Mojo s'acheve le dimanche soir ; les estimations tombent le
    dimanche/lundi. On ne considere donc un week-end comme exploitable qu'a
    partir du lundi qui suit.
    """
    year, week = mojo_week_of(today)
    start, _ = mojo_week_to_dates(year, week)
    if (today - start).days < 3:  # on est encore dans le week-end en cours
        week -= 1
        if week < 1:
            year -= 1
            _, week = mojo_week_of(first_friday(year + 1) - timedelta(days=1))
    return year, week


def mojo_weeks_back(today: date, count: int) -> list[tuple[int, int]]:
    """Les `count` derniers week-ends termines, du plus recent au plus ancien."""
    year, week = latest_complete_mojo_week(today)
    out: list[tuple[int, int]] = []
    cursor = mojo_week_to_dates(year, week)[0]
    for _ in range(count):
        y, w = mojo_week_of(cursor)
        out.append((y, w))
        cursor -= timedelta(days=7)
    return out


# --------------------------------------------------------------------------- #
# Allocine / semaine cinema francaise
# --------------------------------------------------------------------------- #
def french_week_start(day: date) -> date:
    """Mercredi ouvrant la semaine cinema qui contient `day`."""
    return day - timedelta(days=(day.weekday() - WEDNESDAY) % 7)


def latest_complete_french_week(today: date) -> date:
    """Mercredi de la derniere semaine cinema complete (donc deja publiee)."""
    current = french_week_start(today)
    return current - timedelta(days=7)


def french_weeks_back(today: date, count: int) -> list[date]:
    last = latest_complete_french_week(today)
    return [last - timedelta(days=7 * i) for i in range(count)]


def french_week_bounds(start: date) -> tuple[date, date]:
    return start, start + timedelta(days=6)


# --------------------------------------------------------------------------- #
# Semaines ISO (Steam, rapports)
# --------------------------------------------------------------------------- #
def iso_week_start(day: date) -> date:
    """Lundi de la semaine ISO contenant `day`."""
    return day - timedelta(days=day.weekday())


def latest_complete_iso_week(today: date) -> date:
    return iso_week_start(today) - timedelta(days=7)


def iso_weeks_back(today: date, count: int) -> list[date]:
    last = latest_complete_iso_week(today)
    return [last - timedelta(days=7 * i) for i in range(count)]


def from_unix(ts: int | float) -> date:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def to_unix(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# Affichage
# --------------------------------------------------------------------------- #
MONTHS_FR = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]


def fr_date(day: date) -> str:
    return f"{day.day} {MONTHS_FR[day.month - 1]} {day.year}"


def fr_range(start: date, end: date) -> str:
    if start.month == end.month and start.year == end.year:
        return f"{start.day} - {end.day} {MONTHS_FR[end.month - 1]} {end.year}"
    if start.year == end.year:
        return (
            f"{start.day} {MONTHS_FR[start.month - 1]} - "
            f"{end.day} {MONTHS_FR[end.month - 1]} {end.year}"
        )
    return f"{fr_date(start)} - {fr_date(end)}"
