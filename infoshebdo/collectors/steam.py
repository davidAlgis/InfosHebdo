"""Steam : classements officiels Valve.

Deux endpoints publics, sans cle d'API :

1. IStoreTopSellersService/GetWeeklyTopSellers
   Le classement hebdomadaire des meilleures ventes, **par pays**. C'est la
   source qui alimente store.steampowered.com/charts/topselling.
   Valve ne publie ni unites ni chiffre d'affaires : on recupere un rang, le
   rang de la semaine precedente et le nombre de semaines consecutives dans
   le classement. Un rang reste exploitable pour suivre des tendances, mais
   il ne se somme pas et ne se compare pas entre pays.

2. ISteamChartsService/GetGamesByConcurrentPlayers
   Joueurs simultanes, mondial, instantane. C'est une photo au moment de la
   collecte, d'ou une collecte quotidienne qui construit la serie.

Detail d'implementation : ces endpoints n'acceptent pas les parametres a plat
(`?country_code=FR`), il faut passer la requete entiere en JSON dans
`input_json`. Sans cela l'API repond 200 avec un objet vide.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from .. import weeks
from ..http import FetchError
from .base import (
    CCU,
    OFFICIAL,
    RANK,
    STEAM,
    Collector,
    CollectorResult,
    Observation,
    register,
)

log = logging.getLogger(__name__)

TOP_SELLERS_URL = (
    "https://api.steampowered.com/IStoreTopSellersService/GetWeeklyTopSellers/v1/"
)
CONCURRENTS_URL = (
    "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/"
)
STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
STORE_APP_URL = "https://store.steampowered.com/app/{appid}/"

PAGE_COUNT = 100
# GetItems accepte des lots ; 100 identifiants par appel passe sans souci.
NAME_BATCH = 100


def resolve_names(client, appids: list[int]) -> dict[int, str]:
    """appid -> nom du jeu, via l'API officielle du magasin.

    Les classements de joueurs simultanes ne renvoient que des appid. Sans
    cette resolution le rapport afficherait des nombres a la place des titres.
    """
    names: dict[int, str] = {}
    for offset in range(0, len(appids), NAME_BATCH):
        batch = appids[offset : offset + NAME_BATCH]
        payload = {
            "ids": [{"appid": int(a)} for a in batch],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_release": True},
        }
        try:
            data = client.get_json(
                STORE_ITEMS_URL,
                params={"input_json": json.dumps(payload, separators=(",", ":"))},
            )
        except FetchError as exc:
            log.debug("resolution des noms Steam echouee : %s", exc)
            continue
        for item in ((data or {}).get("response", {}) or {}).get("store_items", []):
            appid = item.get("appid")
            name = item.get("name")
            if appid and name:
                names[int(appid)] = name
    return names


def _payload(country: str | None, start_date: int | None) -> dict:
    """Construit input_json.

    `country_code` au niveau racine est ce qui change reellement le
    classement. Le `country_code` de `context` ne fait que choisir la devise
    d'affichage : l'omettre ou le changer seul renvoie le classement mondial.
    `data_request` doit etre non vide, sinon l'API ne renvoie aucun rang.
    """
    body: dict = {
        "page_start": 0,
        "page_count": PAGE_COUNT,
        "context": {"language": "english", "country_code": country or "US"},
        "data_request": {"include_release": True},
    }
    if country:
        body["country_code"] = country
    if start_date:
        body["start_date"] = int(start_date)
    return body


def _request(client, country: str | None, start_date: int | None) -> dict:
    payload = _payload(country, start_date)
    data = client.get_json(
        TOP_SELLERS_URL, params={"input_json": json.dumps(payload, separators=(",", ":"))}
    )
    return (data or {}).get("response", {}) or {}


@register
class SteamTopSellersCollector(Collector):
    name = "steam_topsellers"
    label = "Steam - meilleures ventes hebdomadaires (officiel)"
    domain = STEAM
    source_url = "https://store.steampowered.com/charts/topselling/global"
    provides = "Top 100 hebdomadaire par pays : rang, rang S-1, semaines consecutives"
    reliability = OFFICIAL

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        markets = list(self.config.games.get("steam_markets", []))
        if not markets:
            result.notes.append("Aucun marche Steam configure.")
            return result

        for market in markets:
            country = None if market == "WW" else market
            try:
                current = _request(self.client, country, None)
            except FetchError as exc:
                result.notes.append(f"{market} : {exc}")
                continue

            latest_start = current.get("start_date")
            if not latest_start or not current.get("ranks"):
                result.notes.append(f"{market} : reponse Steam vide.")
                continue

            result.observations.extend(
                self._to_observations(current, market, int(latest_start))
            )

            # Rattrapage : les semaines precedentes ne bougent plus, mais elles
            # comblent l'historique lors d'une premiere installation.
            for offset in range(1, self.backfill):
                past = int(latest_start) - offset * 7 * 86400
                try:
                    payload = _request(self.client, country, past)
                except FetchError as exc:
                    result.notes.append(f"{market} semaine {past} : {exc}")
                    break
                if not payload.get("ranks"):
                    break
                result.observations.extend(
                    self._to_observations(payload, market, past)
                )
        return result

    # ------------------------------------------------------------------ #
    def _to_observations(self, payload: dict, market: str, start_ts: int) -> list[Observation]:
        start = weeks.from_unix(payload.get("start_date") or start_ts)
        end = start + timedelta(days=6)
        label = f"semaine du {weeks.fr_date(start)}"
        out: list[Observation] = []

        for entry in payload.get("ranks", []):
            rank = entry.get("rank")
            appid = entry.get("appid")
            item = entry.get("item") or {}
            title = item.get("name") or (f"appid {appid}" if appid else None)
            if rank is None or not title:
                continue

            previous = entry.get("last_week_rank")
            extra = {
                "consecutive_weeks": entry.get("consecutive_weeks"),
                "store_url": STORE_APP_URL.format(appid=appid) if appid else None,
                "new_entry": previous is None,
            }
            extra = {k: v for k, v in extra.items() if v is not None}

            out.append(
                Observation(
                    source=self.name,
                    domain=STEAM,
                    market=market,
                    period_type="week",
                    period_start=start,
                    period_end=end,
                    period_label=label,
                    title=title,
                    entity_id=str(appid) if appid else None,
                    platform="Steam",
                    metric=RANK,
                    value=float(rank),
                    rank=int(rank),
                    prev_rank=int(previous) if previous else None,
                    reliability=OFFICIAL,
                    extra=extra,
                )
            )
        return out


@register
class SteamConcurrentsCollector(Collector):
    name = "steam_concurrents"
    label = "Steam - joueurs simultanes (officiel, mondial)"
    domain = STEAM
    source_url = "https://store.steampowered.com/charts/mostplayed"
    provides = "Joueurs simultanes et pic du jour, top 100 mondial"
    reliability = OFFICIAL

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        try:
            data = self.client.get_json(CONCURRENTS_URL)
        except FetchError as exc:
            result.notes.append(str(exc))
            return result

        payload = (data or {}).get("response", {}) or {}
        entries = payload.get("ranks", [])
        if not entries:
            result.notes.append("Reponse Steam vide pour les joueurs simultanes.")
            return result

        appids = [e["appid"] for e in entries if e.get("appid")]
        names = resolve_names(self.client, appids)

        # L'API donne un instantane : on l'horodate au jour de la collecte.
        for entry in entries:
            appid = entry.get("appid")
            rank = entry.get("rank")
            current = entry.get("concurrent_in_game")
            if appid is None or current is None:
                continue
            result.observations.append(
                Observation(
                    source=self.name,
                    domain=STEAM,
                    market="WW",
                    period_type="snapshot",
                    period_start=today,
                    period_end=today,
                    period_label=weeks.fr_date(today),
                    title=names.get(int(appid), f"appid {appid}"),
                    entity_id=str(appid),
                    platform="Steam",
                    metric=CCU,
                    value=float(current),
                    rank=int(rank) if rank else None,
                    reliability=OFFICIAL,
                    extra={
                        "peak_in_game": entry.get("peak_in_game"),
                        "store_url": STORE_APP_URL.format(appid=appid),
                    },
                )
            )
        unresolved = sum(1 for o in result.observations if o.title.startswith("appid "))
        if unresolved:
            result.notes.append(f"{unresolved} titres non resolus (appid conserve).")
        return result
