"""Box Office Mojo : recettes de week-end, en dollars.

URL : https://www.boxofficemojo.com/weekend/<annee>W<semaine>/[?area=XX]

Ce qu'il faut savoir avant de lire les chiffres :

* La zone par defaut (sans `area`) est le marche nord-americain ("domestic"),
  et c'est la seule ou la couverture est exhaustive.
* France et Japon ne sont alimentes que par les distributeurs qui declarent
  leurs chiffres a IMDbPro, avec un retard de deux a cinq semaines et des
  revisions. D'ou le rattrapage sur plusieurs semaines a chaque collecte :
  une semaine vide aujourd'hui se remplira plus tard.
* Tout est converti en dollars par Mojo, y compris pour la France et le
  Japon. Pour la France, la donnee en entrees d'Allocine est preferable.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from .. import weeks
from ..http import FetchError
from .base import (
    BOX_OFFICE,
    ESTIMATED,
    GROSS_USD,
    GROSS_USD_CUM,
    OFFICIAL,
    PARTIAL,
    Collector,
    CollectorResult,
    Observation,
    register,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.boxofficemojo.com/weekend/{year}W{week:02d}/"

# Fiabilite de reference par zone : hors Amerique du Nord, Mojo ne voit
# qu'une partie du marche.
AREA_RELIABILITY = {"US": OFFICIAL, "FR": PARTIAL, "JP": PARTIAL}

MONEY_RE = re.compile(r"[^0-9.\-]")


def parse_money(text: str) -> float | None:
    """'$70,711,990' -> 70711990.0 ; '-' ou '' -> None."""
    if not text:
        return None
    cleaned = MONEY_RE.sub("", text.replace(",", ""))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(text: str) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[^0-9\-]", "", text)
    if cleaned in ("", "-"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _cell_text(cell) -> str:
    return cell.get_text(" ", strip=True)


def parse_weekend_table(html: str) -> list[dict]:
    """Extrait les lignes du tableau de week-end, indexees par en-tete.

    Les colonnes de Mojo varient selon la zone (pas de nombre de salles hors
    Amerique du Nord). On travaille donc par nom d'en-tete, jamais par
    position.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [_cell_text(c).lower() for c in rows[0].find_all(["th", "td"])]
    out: list[dict] = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        record: dict[str, str] = {}
        for idx, cell in enumerate(cells):
            if idx < len(headers):
                record[headers[idx]] = _cell_text(cell)
        # Le titre porte le lien vers la fiche du film : on en tire un id.
        link = cells[2].find("a", href=True) if len(cells) > 2 else None
        if link:
            match = re.search(r"/release/(rl\d+)", link["href"])
            if match:
                record["_release_id"] = match.group(1)
        out.append(record)
    return out


def _get(record: dict, *names: str) -> str:
    for name in names:
        for key, value in record.items():
            if key.startswith(name):
                return value
    return ""


@register
class BoxOfficeMojoCollector(Collector):
    name = "boxofficemojo"
    label = "Box Office Mojo (week-end, USD)"
    domain = BOX_OFFICE
    source_url = "https://www.boxofficemojo.com/weekend/"
    provides = "Recettes de week-end en USD : Etats-Unis (exhaustif), France et Japon (partiels)"
    reliability = OFFICIAL

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        areas: dict = self.config.box_office.get("mojo_areas", {}) or {}
        wanted = [m for m in self.config.box_office.get("markets", []) if m in areas]
        if not wanted:
            result.notes.append("Aucun marche Mojo configure.")
            return result

        target_weeks = weeks.mojo_weeks_back(today, self.backfill)
        for market in wanted:
            area = areas.get(market) or ""
            found_any = False
            for year, week in target_weeks:
                try:
                    rows = self._fetch_week(year, week, area)
                except FetchError as exc:
                    result.notes.append(f"{market} {year}W{week:02d} : {exc}")
                    continue
                if not rows:
                    continue
                found_any = True
                result.observations.extend(
                    self._to_observations(rows, market, year, week)
                )
            if not found_any:
                result.notes.append(
                    f"{market} : aucune donnee Mojo sur les {self.backfill} dernieres "
                    "semaines (publication en retard ou zone non alimentee)."
                )
        return result

    # ------------------------------------------------------------------ #
    def _fetch_week(self, year: int, week: int, area: str) -> list[dict]:
        url = BASE_URL.format(year=year, week=week)
        params = {"area": area} if area else None
        html = self.client.get_text(url, params=params)
        return parse_weekend_table(html)

    def _to_observations(
        self, rows: list[dict], market: str, year: int, week: int
    ) -> list[Observation]:
        start, end = weeks.mojo_week_to_dates(year, week)
        label = f"{year}W{week:02d}"
        base_reliability = AREA_RELIABILITY.get(market, PARTIAL)
        out: list[Observation] = []

        for record in rows:
            title = _get(record, "release", "title")
            if not title:
                continue
            gross = parse_money(_get(record, "gross"))
            if gross is None:
                continue

            is_estimate = _get(record, "estimated").strip().lower() == "true"
            reliability = ESTIMATED if is_estimate else base_reliability

            extra = {
                "theaters": parse_int(_get(record, "theaters")),
                "average": parse_money(_get(record, "average")),
                "weeks_in_release": parse_int(_get(record, "weeks")),
                "change_vs_last_week": _get(record, "%") or None,
                "new_this_week": _get(record, "new this week").lower() == "true",
                "mojo_week": label,
            }
            extra = {k: v for k, v in extra.items() if v not in (None, "")}

            common = dict(
                source=self.name,
                domain=BOX_OFFICE,
                market=market,
                period_type="weekend",
                period_start=start,
                period_end=end,
                period_label=label,
                title=title,
                rank=parse_int(_get(record, "rank", "tw")),
                prev_rank=parse_int(_get(record, "lw")),
                entity_id=record.get("_release_id"),
                distributor=_get(record, "distributor") or None,
                currency="USD",
                reliability=reliability,
            )
            out.append(
                Observation(metric=GROSS_USD, value=gross, extra=extra, **common)
            )

            cumulative = parse_money(_get(record, "total gross"))
            if cumulative is not None:
                out.append(
                    Observation(metric=GROSS_USD_CUM, value=cumulative, **common)
                )
        return out
