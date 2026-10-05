"""Contrat commun a tous les collecteurs.

Un collecteur = une source. Il produit des `Observation` et ne touche jamais
la base : c'est l'orchestrateur (`pipeline.py`) qui ecrit, journalise et
encaisse les pannes. Une source en panne ne doit jamais empecher le rapport
de partir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Iterator

# --------------------------------------------------------------------------- #
# Vocabulaire ferme : evite les fautes de frappe qui creeraient des categories
# fantomes dans la base.
# --------------------------------------------------------------------------- #

# Domaines
BOX_OFFICE = "box_office"
STEAM = "steam"
GAMES_ALL = "games_all"

# Metriques
GROSS_USD = "gross_usd"          # recettes converties en dollars (Mojo)
ADMISSIONS = "admissions"        # entrees salles (France)
ADMISSIONS_CUM = "admissions_cumul"
GROSS_USD_CUM = "gross_usd_cumul"
RANK = "rank"                    # classement, sans volume associe
CCU = "concurrent_players"       # joueurs simultanes
OWNERS_EST = "owners_estimate"   # proprietaires estimes (SteamSpy)
UNITS = "units"                  # unites vendues (import manuel)

# Fiabilite : affichee telle quelle dans le rapport, c'est le point clef de
# l'outil. On ne melange jamais un chiffre officiel et une estimation sans le
# dire.
OFFICIAL = "officiel"
ESTIMATED = "estime"
PARTIAL = "partiel"
EXTRAPOLATED = "extrapole"
THIRD_PARTY = "tiers"
MANUAL = "manuel"

RELIABILITY_HELP = {
    OFFICIAL: "Chiffre publie par la source de reference du marche.",
    ESTIMATED: "Estimation d'un tiers, methodologie non verifiable.",
    PARTIAL: "Chiffre officiel mais couverture incomplete du marche.",
    EXTRAPOLATED: "Calcule par InfosHebdo a partir d'autres marches.",
    THIRD_PARTY: "Repris d'un media, non verifie a la source.",
    MANUAL: "Saisi a la main depuis un rapport payant ou un communique.",
}

# Libelles de marche pour le rapport
MARKET_LABELS = {
    "FR": "France",
    "US": "Etats-Unis",
    "JP": "Japon",
    "GB": "Royaume-Uni",
    "DE": "Allemagne",
    "EU": "Europe",
    "WW": "Monde",
}


def market_label(code: str) -> str:
    return MARKET_LABELS.get(code, code)


@dataclass(slots=True)
class Observation:
    """Une mesure atomique, prete a etre stockee."""

    source: str
    domain: str
    market: str
    period_type: str          # 'weekend' | 'week' | 'snapshot'
    period_start: date
    title: str
    metric: str
    reliability: str
    value: float | None = None
    period_end: date | None = None
    period_label: str | None = None
    rank: int | None = None
    prev_rank: int | None = None
    entity_id: str | None = None
    platform: str = ""
    distributor: str | None = None
    currency: str | None = None
    extra: dict[str, Any] | None = None


@dataclass
class CollectorResult:
    observations: list[Observation] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Collector:
    """Classe de base. Sous-classer et implementer `run`."""

    name: str = "base"
    label: str = "Collecteur"
    domain: str = BOX_OFFICE
    # Documentation affichee par `infoshebdo sources`
    source_url: str = ""
    provides: str = ""
    reliability: str = OFFICIAL

    def __init__(self, client, config) -> None:
        self.client = client
        self.config = config

    # -- a implementer -------------------------------------------------- #
    def run(self, today: date) -> CollectorResult:
        raise NotImplementedError

    # -- utilitaires ---------------------------------------------------- #
    @property
    def backfill(self) -> int:
        return self.config.backfill_weeks

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"


# --------------------------------------------------------------------------- #
# Registre
# --------------------------------------------------------------------------- #
_REGISTRY: list[type[Collector]] = []


def register(cls: type[Collector]) -> type[Collector]:
    _REGISTRY.append(cls)
    return cls


def registry() -> list[type[Collector]]:
    return list(_REGISTRY)


def flatten(chunks: Iterable[Iterable[Observation]]) -> Iterator[Observation]:
    for chunk in chunks:
        yield from chunk
