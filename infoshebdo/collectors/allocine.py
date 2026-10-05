"""Allocine : box-office France en entrees.

URL : https://www.allocine.fr/boxoffice/france/sem-<AAAA-MM-JJ>/
ou la date est le mercredi ouvrant la semaine cinema francaise.

C'est la meilleure donnee France disponible sans abonnement : des entrees
salles (et non des recettes converties en dollars comme chez Mojo), avec le
cumul depuis la sortie et le numero de semaine d'exploitation. La source
amont est le comptage professionnel du marche francais ; on la classe donc
comme officielle, en gardant a l'esprit qu'Allocine n'est qu'un relais.

On interroge toujours des URL de semaine explicites plutot que la page
d'accueil du box-office : la periode couverte est alors certaine, sans avoir
a deviner ce que le site affiche par defaut.

Piege verifie sur le site : une partie des pages d'archive rend un tableau
bien forme mais tronque a quatre ou cinq films confidentiels, avec des
entrees a trois chiffres. Elles sont indiscernables d'une semaine normale au
niveau du HTML, d'ou le controle de completude (MIN_ROWS) et la relecture de
la semaine annoncee dans le titre de la page.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from .. import weeks
from ..http import FetchError
from .base import (
    ADMISSIONS,
    ADMISSIONS_CUM,
    BOX_OFFICE,
    OFFICIAL,
    Collector,
    CollectorResult,
    Observation,
    register,
)

log = logging.getLogger(__name__)

WEEK_URL = "https://www.allocine.fr/boxoffice/france/sem-{day}/"

# Le classement francais est toujours un top 10. Certaines pages d'archive
# renvoient une liste tronquee de quatre ou cinq films confidentiels : le
# tableau existe, la structure est bonne, mais son contenu ne correspond pas
# au box-office de la semaine. Sans ce seuil, ces pages ecraseraient
# l'historique avec des chiffres a trois chiffres et fausseraient toutes les
# evolutions calculees ensuite.
MIN_ROWS = 10

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    "décembre": 12,
}

# "Box Office Cinema - Semaine du mercredi 12 aout 2026"
WEEK_LABEL_RE = re.compile(
    r"semaine\s+du\s+\w+\s+(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})", re.IGNORECASE
)

# Espaces fines et insecables utilisees comme separateur de milliers.
SPACES = re.compile(r"[\s   ]")


def parse_count(text: str) -> int | None:
    """'1 189 232' -> 1189232."""
    if not text:
        return None
    cleaned = SPACES.sub("", text)
    cleaned = re.sub(r"[^0-9]", "", cleaned)
    return int(cleaned) if cleaned else None


def parse_week_label(soup: BeautifulSoup) -> date | None:
    """Lit la semaine annoncee par la page elle-meme.

    Le titre de la page porte « Semaine du mercredi 12 aout 2026 ». On le lit
    plutot que de faire confiance a l'URL demandee : si Allocine redirige ou
    sert une autre semaine, l'observation serait rangee sous une periode
    fausse, ce qui est bien pire qu'une semaine manquante.
    """
    heading = soup.find("h1")
    if heading is None:
        return None
    match = WEEK_LABEL_RE.search(heading.get_text(" ", strip=True))
    if not match:
        return None
    day, month_name, year = match.groups()
    month = MONTHS_FR.get(month_name.lower())
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def parse_box_office_table(html: str) -> list[dict]:
    """Extrait les lignes du tableau box-office d'une page semaine."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.box-office-table")
    if table is None:
        return []

    out: list[dict] = []
    for tr in table.select("tr.responsive-table-row"):
        title_link = tr.select_one("a.meta-title-link")
        if title_link is None:
            continue

        rank_tag = tr.select_one(".label-ranking")
        distributor_tag = tr.select_one(".meta-body")

        cells = {
            (td.get("data-heading") or "").strip().lower(): td.get_text(" ", strip=True)
            for td in tr.select("td[data-heading]")
        }

        film_id = None
        match = re.search(r"cfilm=(\d+)", title_link.get("href", ""))
        if match:
            film_id = match.group(1)

        out.append(
            {
                "rank": parse_count(rank_tag.get_text(strip=True)) if rank_tag else None,
                "title": title_link.get_text(" ", strip=True),
                "film_id": film_id,
                "distributor": (
                    distributor_tag.get_text(" ", strip=True) if distributor_tag else None
                ),
                "admissions": parse_count(cells.get("entrées") or cells.get("entrees", "")),
                "cumulative": parse_count(cells.get("cumul", "")),
                "week_in_release": parse_count(cells.get("semaine", "")),
            }
        )
    return out


@register
class AllocineFranceCollector(Collector):
    name = "allocine_france"
    label = "Allocine - box-office France (entrees)"
    domain = BOX_OFFICE
    source_url = "https://www.allocine.fr/boxoffice/france/"
    provides = "Entrees hebdomadaires France, cumul et semaine d'exploitation"
    reliability = OFFICIAL

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        if "FR" not in self.config.box_office.get("markets", []):
            result.notes.append("Marche FR non demande, collecteur ignore.")
            return result

        for start in weeks.french_weeks_back(today, self.backfill):
            url = WEEK_URL.format(day=start.isoformat())
            try:
                html = self.client.get_text(url)
            except FetchError as exc:
                result.notes.append(f"semaine du {start} : {exc}")
                continue

            soup = BeautifulSoup(html, "lxml")
            announced = parse_week_label(soup)
            if announced and announced != start:
                result.notes.append(
                    f"semaine du {start} : la page annonce le {announced}, ignoree."
                )
                continue

            rows = parse_box_office_table(html)
            if not rows:
                result.notes.append(
                    f"semaine du {start} : tableau vide ou structure modifiee."
                )
                continue
            if len(rows) < MIN_ROWS:
                result.notes.append(
                    f"semaine du {start} : archive tronquee ({len(rows)} film(s) au "
                    f"lieu de {MIN_ROWS}), ignoree pour ne pas fausser l'historique."
                )
                continue
            result.observations.extend(self._to_observations(rows, start))
        return result

    # ------------------------------------------------------------------ #
    def _to_observations(self, rows: list[dict], start: date) -> list[Observation]:
        period_start, period_end = weeks.french_week_bounds(start)
        label = f"semaine du {weeks.fr_date(period_start)}"
        out: list[Observation] = []

        for row in rows:
            if row["admissions"] is None or not row["title"]:
                continue
            extra = {"week_in_release": row["week_in_release"]}
            extra = {k: v for k, v in extra.items() if v is not None}

            common = dict(
                source=self.name,
                domain=BOX_OFFICE,
                market="FR",
                period_type="week",
                period_start=period_start,
                period_end=period_end,
                period_label=label,
                title=row["title"],
                rank=row["rank"],
                entity_id=row["film_id"],
                distributor=row["distributor"],
                currency=None,
                reliability=OFFICIAL,
            )
            out.append(
                Observation(
                    metric=ADMISSIONS,
                    value=float(row["admissions"]),
                    extra=extra or None,
                    **common,
                )
            )
            if row["cumulative"] is not None:
                out.append(
                    Observation(
                        metric=ADMISSIONS_CUM, value=float(row["cumulative"]), **common
                    )
                )
        return out
