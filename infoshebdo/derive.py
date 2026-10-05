"""Donnees calculees a partir de la base, pas collectees.

Il n'existe pas de classement hebdomadaire mondial du box-office en acces
libre : Box Office Mojo publie un cumul annuel monde, mais pas de tableau
semaine par semaine (la zone `XWW` renvoie une page vide).

On reconstitue donc un « monde » par sommation des zones effectivement
collectees. Ce n'est pas le box-office mondial : c'est la somme des marches
suivis, et rien d'autre. La fiabilite est marquee `extrapole` et la liste des
marches inclus voyage avec la donnee, pour que le rapport puisse afficher
exactement ce que le chiffre recouvre.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date

from .collectors.base import (
    BOX_OFFICE,
    EXTRAPOLATED,
    GROSS_USD,
    Observation,
)

AGGREGATE_SOURCE = "agregat_infoshebdo"


def _iso_to_date(value: str) -> date:
    return date.fromisoformat(value)


def worldwide_box_office(
    conn: sqlite3.Connection, markets: list[str], weeks_back: int = 8
) -> list[Observation]:
    """Somme les recettes USD des zones collectees, par week-end et par film.

    Un film n'est retenu que si au moins une zone le declare sur la periode ;
    `extra.markets_included` dit lesquelles, ce qui evite de lire 70 M$ comme
    un chiffre mondial quand seule l'Amerique du Nord a repondu.
    """
    source_markets = [m for m in markets if m != "WW"]
    if not source_markets:
        return []

    placeholders = ",".join("?" for _ in source_markets)
    periods = [
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT period_start FROM observations"
            f" WHERE domain = ? AND metric = ? AND market IN ({placeholders})"
            f" AND source != ?"
            f" ORDER BY period_start DESC LIMIT ?",
            [BOX_OFFICE, GROSS_USD, *source_markets, AGGREGATE_SOURCE, weeks_back],
        )
    ]
    if not periods:
        return []

    out: list[Observation] = []
    for period in periods:
        rows = conn.execute(
            f"SELECT market, title, value, period_end, period_label, distributor,"
            f" entity_id, reliability FROM observations"
            f" WHERE domain = ? AND metric = ? AND period_start = ?"
            f" AND market IN ({placeholders}) AND source != ? AND value IS NOT NULL",
            [BOX_OFFICE, GROSS_USD, period, *source_markets, AGGREGATE_SOURCE],
        ).fetchall()
        if not rows:
            continue

        totals: dict[str, dict] = defaultdict(
            lambda: {
                "value": 0.0,
                "markets": {},
                "distributor": None,
                "period_end": None,
                "period_label": None,
                "entity_id": None,
            }
        )
        for row in rows:
            bucket = totals[row["title"]]
            # Une meme zone peut apparaitre deux fois si deux sources la
            # couvrent : on garde la plus elevee plutot que de cumuler.
            previous = bucket["markets"].get(row["market"], 0.0)
            if row["value"] > previous:
                bucket["markets"][row["market"]] = row["value"]
            bucket["distributor"] = bucket["distributor"] or row["distributor"]
            bucket["period_end"] = bucket["period_end"] or row["period_end"]
            bucket["period_label"] = bucket["period_label"] or row["period_label"]
            bucket["entity_id"] = bucket["entity_id"] or row["entity_id"]

        ranked = sorted(
            totals.items(), key=lambda kv: sum(kv[1]["markets"].values()), reverse=True
        )
        for rank, (title, bucket) in enumerate(ranked, start=1):
            included = sorted(bucket["markets"])
            out.append(
                Observation(
                    source=AGGREGATE_SOURCE,
                    domain=BOX_OFFICE,
                    market="WW",
                    period_type="weekend",
                    period_start=_iso_to_date(period),
                    period_end=(
                        _iso_to_date(bucket["period_end"]) if bucket["period_end"] else None
                    ),
                    period_label=bucket["period_label"],
                    title=title,
                    entity_id=bucket["entity_id"],
                    distributor=bucket["distributor"],
                    metric=GROSS_USD,
                    value=sum(bucket["markets"].values()),
                    currency="USD",
                    rank=rank,
                    reliability=EXTRAPOLATED,
                    extra={
                        "markets_included": included,
                        "markets_expected": source_markets,
                        "per_market": bucket["markets"],
                        "coverage": (
                            "complete"
                            if set(included) == set(source_markets)
                            else "partielle"
                        ),
                    },
                )
            )
    return out


def resolve_titles(conn: sqlite3.Connection) -> int:
    """Remplace les titres 'appid NNN' par un nom trouve ailleurs en base.

    Les releves de joueurs simultanes precedent parfois la resolution des
    noms (API indisponible au moment de la collecte). On rattrape ici a
    partir des classements de ventes, qui portent les titres.
    """
    known = {
        row["entity_id"]: row["title"]
        for row in conn.execute(
            "SELECT DISTINCT entity_id, title FROM observations"
            " WHERE entity_id IS NOT NULL AND title NOT LIKE 'appid %'"
            " AND platform = 'Steam'"
        )
    }
    if not known:
        return 0

    pending = conn.execute(
        "SELECT id, entity_id FROM observations WHERE title LIKE 'appid %'"
    ).fetchall()
    fixed = 0
    for row in pending:
        name = known.get(row["entity_id"])
        if not name:
            continue
        try:
            conn.execute(
                "UPDATE observations SET title = ? WHERE id = ?", (name, row["id"])
            )
        except sqlite3.IntegrityError:
            # Le releve existe deja sous son vrai nom : la ligne 'appid NNN'
            # est un doublon, on la supprime.
            conn.execute("DELETE FROM observations WHERE id = ?", (row["id"],))
        fixed += 1
    return fixed


def json_extra(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}
