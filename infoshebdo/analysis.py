"""Construction du rapport hebdomadaire a partir de la base.

Ce module ne fait aucun acces reseau : il lit ce qui a ete collecte et calcule
les evolutions. Deux principes :

* Chaque tableau porte sa propre periode. Les sources n'ont pas la meme
  semaine (week-end vendredi-dimanche chez Mojo, mercredi-mardi pour le
  cinema francais, mardi-lundi chez Steam) et les aligner de force ferait
  mentir les chiffres.
* Chaque tableau porte sa fiabilite et, s'il y a lieu, un avertissement. Une
  section vide est affichee comme vide, avec la raison.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import db, sources, weeks
from .collectors.base import (
    ADMISSIONS,
    ADMISSIONS_CUM,
    BOX_OFFICE,
    CCU,
    GAMES_ALL,
    GROSS_USD,
    GROSS_USD_CUM,
    OWNERS_EST,
    RANK,
    RELIABILITY_HELP,
    STEAM,
    THIRD_PARTY,
    UNITS,
    market_label,
)
from .derive import json_extra
from .sources import Link

NBSP = " "  # espace fine insecable, separateur de milliers francais


# --------------------------------------------------------------------------- #
# Mise en forme
# --------------------------------------------------------------------------- #
def fmt_int(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}".replace(",", NBSP)


def fmt_money(value: float | None, currency: str | None = "USD") -> str:
    if value is None:
        return "-"
    symbol = {"USD": "$", "EUR": "€"}.get(currency or "", "")
    if abs(value) >= 1_000_000:
        return f"{symbol}{value / 1_000_000:.1f}{NBSP}M".replace(".", ",")
    return f"{symbol}{fmt_int(value)}"


def fmt_delta_pct(delta: float | None) -> str:
    if delta is None:
        return ""
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f}%".replace(".", ",")


def rank_move(rank: int | None, prev: int | None, comparable: bool = True) -> tuple[str, str]:
    """(symbole, classe CSS) pour l'evolution de rang.

    `comparable` a False signifie qu'aucune periode precedente n'existe en
    base : on n'affiche alors rien plutot que d'annoncer une fausse nouveaute.
    """
    if rank is None or not comparable:
        return "", "flat"
    if prev is None:
        return "nouveau", "new"
    if prev > rank:
        return f"▲ {prev - rank}", "up"
    if prev < rank:
        return f"▼ {rank - prev}", "down"
    return "=", "flat"


# --------------------------------------------------------------------------- #
# Structures passees au gabarit
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    rank: int | None
    title: str
    value_fmt: str
    value: float | None = None
    prev_rank: int | None = None
    move: str = ""
    move_class: str = "flat"
    delta_pct: str = ""
    delta_class: str = "flat"
    secondary: str = ""       # cumul, pic, fourchette...
    detail: str = ""          # distributeur, plateforme
    reliability: str = ""
    url: str = ""
    is_new: bool = False


@dataclass
class Table:
    key: str
    title: str
    unit: str
    source_label: str
    reliability: str
    period_label: str = ""
    columns: tuple[str, ...] = ("Rang", "Titre", "Valeur", "vs S-1")
    rows: list[Row] = field(default_factory=list)
    note: str = ""
    warning: str = ""
    # Pages qui publient ce classement : verifier un chiffre en un clic.
    links: list[Link] = field(default_factory=list)
    # Panneau deplie a l'ouverture du rapport. Tous les autres sont plies.
    expanded: bool = False

    @property
    def empty(self) -> bool:
        return not self.rows


@dataclass
class Section:
    key: str
    title: str
    intro: str = ""
    tables: list[Table] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return all(t.empty for t in self.tables) and not self.news


@dataclass
class Report:
    title: str
    generated_at: str
    week_label: str
    period_start: date
    period_end: date
    sections: list[Section] = field(default_factory=list)
    run_log: list[dict] = field(default_factory=list)
    legend: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    headline: str = ""
    notices: list[str] = field(default_factory=list)   # avertissements en tete

    @property
    def has_data(self) -> bool:
        return any(not s.empty for s in self.sections)


# --------------------------------------------------------------------------- #
# Aides de lecture
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    """Periode precedente : valeurs et rangs, indexes par titre.

    Toutes les sources ne donnent pas le rang de la semaine passee. Allocine
    n'en publie aucun ; sans ce rattrapage par la base, chaque film serait
    signale comme une nouveaute chaque semaine. Quand `period` est None, il
    n'y a rien a comparer et on n'affiche aucune evolution.
    """

    period: str | None = None
    gap_days: int = 0
    values: dict[str, float] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)

    # Une semaine peut manquer : source en panne, page d'archive tronquee,
    # premiere installation. Comparer alors a la periode disponible la plus
    # proche produirait un « vs S-1 » qui porte en realite sur un mois.
    MAX_GAP_DAYS = 10

    @property
    def exists(self) -> bool:
        return self.period is not None

    @property
    def adjacent(self) -> bool:
        return self.exists and self.gap_days <= self.MAX_GAP_DAYS

    def is_new(self, title: str) -> bool:
        return self.adjacent and title not in self.ranks and title not in self.values


def _previous_snapshot(
    conn: sqlite3.Connection, domain: str, market: str, metric: str, period: str
) -> Snapshot:
    row = conn.execute(
        "SELECT MAX(period_start) FROM observations"
        " WHERE domain = ? AND market = ? AND metric = ? AND period_start < ?",
        (domain, market, metric, period),
    ).fetchone()
    if not row or not row[0]:
        return Snapshot()

    gap = (date.fromisoformat(period) - date.fromisoformat(row[0])).days
    snapshot = Snapshot(period=row[0], gap_days=gap)
    for record in conn.execute(
        "SELECT title, value, rank FROM observations"
        " WHERE domain = ? AND market = ? AND metric = ? AND period_start = ?",
        (domain, market, metric, row[0]),
    ):
        if record["value"] is not None:
            snapshot.values[record["title"]] = record["value"]
        if record["rank"] is not None:
            snapshot.ranks[record["title"]] = record["rank"]
    return snapshot


def _companion(
    conn: sqlite3.Connection, domain: str, market: str, metric: str, period: str
) -> dict[str, float]:
    """Metrique secondaire (cumul) de la meme periode, indexee par titre."""
    return {
        r["title"]: r["value"]
        for r in conn.execute(
            "SELECT title, value FROM observations"
            " WHERE domain = ? AND market = ? AND metric = ? AND period_start = ?",
            (domain, market, metric, period),
        )
        if r["value"] is not None
    }


def _delta(current: float | None, previous: float | None) -> tuple[str, str]:
    if current is None or previous in (None, 0):
        return "", "flat"
    change = (current - previous) / abs(previous) * 100
    css = "up" if change > 0 else ("down" if change < 0 else "flat")
    return fmt_delta_pct(change), css


def _latest_period(
    conn: sqlite3.Connection, domain: str, market: str, metric: str
) -> str | None:
    periods = db.available_periods(conn, domain, market, metric, limit=1)
    return periods[0] if periods else None


def _period_label(rows: list[sqlite3.Row], fallback: str) -> str:
    for row in rows:
        if row["period_label"]:
            return str(row["period_label"])
        if row["period_start"] and row["period_end"]:
            return weeks.fr_range(
                date.fromisoformat(row["period_start"]),
                date.fromisoformat(row["period_end"]),
            )
    return fallback


# --------------------------------------------------------------------------- #
# Section box-office
# --------------------------------------------------------------------------- #
# Seul le box-office France est deplie a l'ouverture du rapport.
EXPANDED_TABLES = frozenset({"bo_fr"})


def _box_office_table(
    conn: sqlite3.Connection, market: str, top_n: int, mojo_areas: dict | None = None
) -> Table | None:
    """Un tableau par marche : entrees pour la France, recettes USD sinon."""
    areas = mojo_areas or {}
    if market == "FR":
        metric, cumul_metric = ADMISSIONS, ADMISSIONS_CUM
        unit, source = "entrees", "Allocine (comptage professionnel France)"
        column = "Entrees"
    else:
        metric, cumul_metric = GROSS_USD, GROSS_USD_CUM
        unit, source = "recettes USD", "Box Office Mojo"
        column = "Recettes USD"

    period = _latest_period(conn, BOX_OFFICE, market, metric)
    key = f"bo_{market.lower()}"

    if period is None:
        return Table(
            key=key,
            title=f"Box-office {market_label(market)}",
            unit=unit,
            source_label=source,
            reliability="-",
            note="Aucune donnee en base pour ce marche.",
            links=_box_office_links(market, None, areas),
            expanded=key in EXPANDED_TABLES,
        )

    rows = db.chart(conn, BOX_OFFICE, market, metric, period, limit=top_n)
    previous = _previous_snapshot(conn, BOX_OFFICE, market, metric, period)
    cumulative = _companion(conn, BOX_OFFICE, market, cumul_metric, period)

    table = Table(
        key=key,
        title=f"Box-office {market_label(market)}",
        unit=unit,
        source_label=source,
        reliability=rows[0]["reliability"] if rows else "-",
        period_label=_period_label(rows, period),
        columns=("Rang", "Film", column, "vs S-1", "Cumul"),
        expanded=key in EXPANDED_TABLES,
    )
    table.links = _box_office_links(market, period, areas)

    if market == "WW":
        table.source_label = "Somme des zones collectees (calcul InfosHebdo)"
        coverages = {
            json_extra(r["extra"]).get("coverage") for r in rows
        }
        included: set[str] = set()
        for row in rows:
            included.update(json_extra(row["extra"]).get("markets_included") or [])
        # Le « monde » est une somme : ses sources sont les zones additionnees.
        table.links = [
            sources.mojo_week(
                period,
                areas.get(zone, zone),
                f"Box Office Mojo - {market_label(zone)}",
            )
            for zone in sorted(included)
        ]
        if "partielle" in coverages or not included:
            table.warning = (
                "Ce n'est pas le box-office mondial : aucune source libre ne le "
                "publie a la semaine. Somme des zones effectivement remontees "
                f"({', '.join(sorted(market_label(m) for m in included)) or 'aucune'}). "
                "France et Japon arrivent avec plusieurs semaines de retard."
            )

    if previous.exists and not previous.adjacent:
        table.note = (
            f"{table.note} La semaine precedente manque en base (donnee la plus "
            f"proche : {previous.period}) : aucune evolution n'est affichee."
        ).strip()

    for row in rows:
        extra = json_extra(row["extra"])
        delta_txt, delta_css = (
            _delta(row["value"], previous.values.get(row["title"]))
            if previous.adjacent
            else ("", "flat")
        )
        # Le rang S-1 fourni par la source fait foi ; a defaut on le retrouve
        # dans la periode precedente stockee en base.
        prev_rank = row["prev_rank"] or (
            previous.ranks.get(row["title"]) if previous.adjacent else None
        )
        move, move_css = rank_move(
            row["rank"], prev_rank, previous.adjacent or row["prev_rank"] is not None
        )
        if market == "FR":
            value_fmt = fmt_int(row["value"])
            secondary = fmt_int(cumulative.get(row["title"]))
        else:
            value_fmt = fmt_money(row["value"], row["currency"])
            secondary = fmt_money(cumulative.get(row["title"]), row["currency"])

        detail = row["distributor"] or ""
        if market == "WW" and extra.get("per_market"):
            detail = " + ".join(
                f"{market_label(k)} {fmt_money(v)}"
                for k, v in sorted(extra["per_market"].items(), key=lambda kv: -kv[1])
            )
        elif extra.get("theaters"):
            detail = f"{detail} - {fmt_int(extra['theaters'])} salles".strip(" -")
        elif extra.get("week_in_release"):
            detail = f"{detail} - semaine {extra['week_in_release']}".strip(" -")

        table.rows.append(
            Row(
                rank=row["rank"],
                title=row["title"],
                value=row["value"],
                value_fmt=value_fmt,
                prev_rank=prev_rank,
                move=move,
                move_class=move_css,
                delta_pct=delta_txt,
                delta_class=delta_css,
                secondary=secondary,
                detail=detail,
                reliability=row["reliability"],
                is_new=previous.is_new(row["title"]),
            )
        )
    return table


def _box_office_links(market: str, period: str | None, areas: dict) -> list[Link]:
    """Liens de source d'un marche : la semaine precise si on la connait."""
    if market == "FR":
        return [sources.allocine_week(period)]
    if market == "WW":
        return []                       # somme calculee : voir les zones, plus bas
    area = areas.get(market, market)
    return [sources.mojo_week(period, area, f"Box Office Mojo - {market_label(market)}")]


def box_office_section(conn: sqlite3.Connection, config) -> Section:
    markets = list(config.box_office.get("markets", []))
    areas = config.box_office.get("mojo_areas", {}) or {}
    section = Section(
        key="box_office",
        title="Box-office cinema",
        intro=(
            "France en entrees salles, autres marches en recettes converties en "
            "dollars par Box Office Mojo. Les deux unites ne se comparent pas "
            "directement."
        ),
    )
    for market in markets:
        table = _box_office_table(conn, market, config.top_n, areas)
        if table:
            section.tables.append(table)
    return section


# --------------------------------------------------------------------------- #
# Section Steam
# --------------------------------------------------------------------------- #
def _steam_rank_table(conn: sqlite3.Connection, market: str, top_n: int) -> Table:
    period = _latest_period(conn, STEAM, market, RANK)
    key = f"steam_{market.lower()}"
    title = f"Steam - meilleures ventes {market_label(market)}"

    if period is None:
        return Table(
            key=key,
            title=title,
            unit="rang",
            source_label="Steam (Valve)",
            reliability="-",
            note="Aucune donnee en base pour ce marche.",
            links=[sources.steam_topsellers(market)],
        )

    rows = db.chart(conn, STEAM, market, RANK, period, limit=top_n)
    table = Table(
        key=key,
        title=title,
        unit="rang",
        source_label="Steam, classement officiel des ventes",
        links=[sources.steam_topsellers(market)],
        reliability=rows[0]["reliability"] if rows else "-",
        period_label=_period_label(rows, period),
        columns=("Rang", "Jeu", "Semaines", "vs S-1"),
        note=(
            "Valve ne publie ni unites ni chiffre d'affaires : ce classement "
            "indique un ordre, pas un volume. Les rangs ne s'additionnent pas "
            "et ne se comparent pas d'un pays a l'autre."
        ),
    )
    for row in rows:
        extra = json_extra(row["extra"])
        move, move_css = rank_move(row["rank"], row["prev_rank"])
        consecutive = extra.get("consecutive_weeks")
        table.rows.append(
            Row(
                rank=row["rank"],
                title=row["title"],
                value=row["value"],
                value_fmt=f"{consecutive}" if consecutive else "1",
                prev_rank=row["prev_rank"],
                move=move,
                move_class=move_css,
                secondary=f"S-1 : {row['prev_rank']}" if row["prev_rank"] else "entree",
                reliability=row["reliability"],
                url=extra.get("store_url", ""),
                is_new=row["prev_rank"] is None,
            )
        )
    return table


def _steam_ccu_table(conn: sqlite3.Connection, top_n: int) -> Table:
    period = _latest_period(conn, STEAM, "WW", CCU)
    table = Table(
        key="steam_ccu",
        title="Steam - joueurs simultanes (monde)",
        unit="joueurs",
        source_label="Steam, releve du jour",
        links=[sources.steam_most_played()],
        reliability="officiel",
        columns=("Rang", "Jeu", "Joueurs", "vs 7 j", "Pic"),
        note=(
            "Photo instantanee prise au moment de la collecte, pas une moyenne "
            "hebdomadaire. Mesure l'activite, pas les ventes."
        ),
    )
    if period is None:
        table.note = "Aucun releve en base."
        return table

    rows = db.chart(conn, STEAM, "WW", CCU, period, limit=top_n)
    table.period_label = weeks.fr_date(date.fromisoformat(period))

    # Comparaison au releve le plus proche de sept jours plus tot.
    target = (date.fromisoformat(period) - timedelta(days=7)).isoformat()
    reference = conn.execute(
        "SELECT period_start FROM observations"
        " WHERE domain = ? AND market = 'WW' AND metric = ?"
        " ORDER BY ABS(JULIANDAY(period_start) - JULIANDAY(?)) LIMIT 1",
        (STEAM, CCU, target),
    ).fetchone()
    previous: dict[str, float] = {}
    if reference and reference[0] != period:
        previous = _companion(conn, STEAM, "WW", CCU, reference[0])

    for row in rows:
        extra = json_extra(row["extra"])
        delta_txt, delta_css = _delta(row["value"], previous.get(row["title"]))
        table.rows.append(
            Row(
                rank=row["rank"],
                title=row["title"],
                value=row["value"],
                value_fmt=fmt_int(row["value"]),
                delta_pct=delta_txt,
                delta_class=delta_css,
                secondary=fmt_int(extra.get("peak_in_game")),
                reliability=row["reliability"],
                url=extra.get("store_url", ""),
            )
        )
    return table


def _steamspy_table(conn: sqlite3.Connection, top_n: int) -> Table:
    period = _latest_period(conn, STEAM, "WW", OWNERS_EST)
    table = Table(
        key="steamspy",
        title="Steam - proprietaires estimes (monde)",
        unit="proprietaires estimes",
        source_label="SteamSpy (estimation par echantillonnage)",
        links=[sources.steamspy()],
        reliability="estime",
        columns=("Rang", "Jeu", "Proprietaires", "vs 7 j", "Fourchette"),
        note=(
            "Estimation tierce, marge d'erreur large. Le seul indicateur de "
            "volume disponible sur Steam, a ne pas melanger avec un chiffre "
            "officiel."
        ),
    )
    if period is None:
        table.note = "Aucun releve en base."
        return table

    rows = db.chart(conn, STEAM, "WW", OWNERS_EST, period, limit=top_n)
    table.period_label = weeks.fr_date(date.fromisoformat(period))
    for row in rows:
        extra = json_extra(row["extra"])
        table.rows.append(
            Row(
                rank=row["rank"],
                title=row["title"],
                value=row["value"],
                value_fmt=fmt_int(row["value"]),
                secondary=extra.get("owners_range", ""),
                reliability=row["reliability"],
                detail=extra.get("publisher") or "",
            )
        )
    return table


def steam_section(conn: sqlite3.Connection, config) -> Section:
    section = Section(
        key="steam",
        title="Jeu video - Steam",
        intro=(
            "Les classements de ventes par pays sont officiels mais sans volume. "
            "Les indicateurs de volume disponibles (joueurs simultanes, "
            "proprietaires estimes) sont mondiaux uniquement."
        ),
    )
    for market in config.games.get("steam_markets", []):
        section.tables.append(_steam_rank_table(conn, market, config.top_n))
    if config.source_enabled("games", "steam_concurrents"):
        section.tables.append(_steam_ccu_table(conn, config.top_n))
    if config.source_enabled("games", "steamspy"):
        section.tables.append(_steamspy_table(conn, config.top_n))
    return section


# --------------------------------------------------------------------------- #
# Section toutes plateformes
# --------------------------------------------------------------------------- #
def all_platforms_section(conn: sqlite3.Connection, config, since: str) -> Section:
    section = Section(
        key="games_all",
        title="Jeu video - toutes plateformes",
        intro=(
            "Aucune source libre ne publie d'unites hebdomadaires toutes "
            "plateformes : les panels de reference sont payants (GSD pour "
            "l'Europe, Circana aux Etats-Unis, Famitsu au Japon) et les "
            "classements hebdomadaires de VGChartz s'arretent en decembre 2018. "
            "Cette section reunit donc les chiffres importes a la main et le "
            "reperage des publications de classements."
        ),
    )

    markets = sorted(
        {
            r["market"]
            for r in conn.execute(
                "SELECT DISTINCT market FROM observations WHERE domain = ?",
                (GAMES_ALL,),
            )
        }
    )
    for market in markets:
        period = _latest_period(conn, GAMES_ALL, market, UNITS)
        if period is None:
            continue
        rows = db.chart(conn, GAMES_ALL, market, UNITS, period, limit=config.top_n)
        previous = _previous_snapshot(conn, GAMES_ALL, market, UNITS, period)
        table = Table(
            key=f"all_{market.lower()}",
            title=f"Ventes toutes plateformes - {market_label(market)}",
            unit="unites",
            source_label="Import manuel",
            reliability=rows[0]["reliability"] if rows else "-",
            period_label=_period_label(rows, period),
            columns=("Rang", "Jeu", "Unites", "vs S-1", "Plateforme"),
        )
        for row in rows:
            extra = json_extra(row["extra"])
            delta_txt, delta_css = (
                _delta(row["value"], previous.values.get(row["title"]))
                if previous.adjacent
                else ("", "flat")
            )
            prev_rank = row["prev_rank"] or (
                previous.ranks.get(row["title"]) if previous.adjacent else None
            )
            move, move_css = rank_move(
                row["rank"], prev_rank, previous.adjacent or row["prev_rank"] is not None
            )
            # Pour une donnee saisie a la main, la provenance compte autant que
            # le chiffre : on affiche la note du CSV a defaut de distributeur.
            detail = row["distributor"] or extra.get("note") or ""
            table.rows.append(
                Row(
                    rank=row["rank"],
                    title=row["title"],
                    value=row["value"],
                    value_fmt=fmt_int(row["value"]),
                    prev_rank=prev_rank,
                    move=move,
                    move_class=move_css,
                    delta_pct=delta_txt,
                    delta_class=delta_css,
                    secondary=row["platform"] or "",
                    detail=detail,
                    reliability=row["reliability"],
                    is_new=previous.is_new(row["title"]),
                )
            )
        section.tables.append(table)

    if not section.tables:
        section.tables.append(
            Table(
                key="all_empty",
                title="Ventes toutes plateformes",
                unit="unites",
                source_label="Import manuel",
                reliability="-",
                note=(
                    "Aucun chiffre importe. Pour en ajouter : deposer un CSV "
                    "dans data/import/ (colonnes market, period_start, title, "
                    "metric, value) puis relancer la collecte."
                ),
            )
        )

    section.news = [
        {
            "source": r["source"],
            "title": r["title"],
            "url": r["url"],
            "published_at": r["published_at"],
            "markets": [m for m in (r["markets"] or "").split(",") if m],
            "summary": r["summary"],
        }
        for r in db.recent_news(conn, since)
    ]
    return section


# --------------------------------------------------------------------------- #
# Assemblage
# --------------------------------------------------------------------------- #
def _headline(sections: list[Section]) -> str:
    """Resume en une phrase : les tetes de classement des marches cles."""
    tables = {t.key: t for s in sections for t in s.tables if t.rows}
    parts: list[str] = []

    for key, prefix in (("bo_fr", "France"), ("bo_us", "Etats-Unis")):
        table = tables.get(key)
        if table:
            top = table.rows[0]
            suffix = f" {table.unit}" if table.unit == "entrees" else ""
            parts.append(f"{prefix} : {top.title} ({top.value_fmt}{suffix})")

    steam = tables.get("steam_fr") or tables.get("steam_ww")
    if steam:
        parts.append(f"Steam : {steam.rows[0].title}")

    if parts:
        return "En tete cette semaine - " + " ; ".join(parts) + "."
    if tables:
        first = next(iter(tables.values()))
        return f"{first.rows[0].title} en tete de {first.title}."
    return "Aucune donnee collectee sur la periode."


def build_report(config, today: date | None = None, notices: list[str] | None = None) -> Report:
    today = today or date.today()
    period_start = weeks.latest_complete_iso_week(today)
    period_end = period_start + timedelta(days=6)

    with db.session() as conn:
        sections = []
        if config.box_office.get("enabled", True):
            sections.append(box_office_section(conn, config))
        if config.games.get("enabled", True):
            sections.append(steam_section(conn, config))
            sections.append(
                all_platforms_section(conn, config, since=period_start.isoformat())
            )

        run_log = [
            {
                "collector": r["collector"],
                "label": r["label"],
                "status": r["status"],
                "rows": r["rows"],
                "duration_ms": r["duration_ms"],
                "message": r["message"] or "",
            }
            for r in db.last_run_log(conn, "collect")
        ]
        report_stats = db.stats(conn)

    used = {t.reliability for s in sections for t in s.tables if t.reliability != "-"}
    for section in sections:
        if section.news:
            used.add(THIRD_PARTY)
        for table in section.tables:
            for row in table.rows:
                if row.reliability:
                    used.add(row.reliability)

    report = Report(
        title=config.report.get("title", "Veille hebdo"),
        generated_at=weeks.fr_date(today),
        week_label=f"semaine {period_start.isocalendar().week}",
        period_start=period_start,
        period_end=period_end,
        sections=sections,
        run_log=run_log,
        legend={k: v for k, v in RELIABILITY_HELP.items() if k in used},
        stats=report_stats,
        notices=list(notices or []),
    )
    report.headline = _headline(sections)
    return report
