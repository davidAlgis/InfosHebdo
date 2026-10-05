"""SteamSpy : estimation du nombre de proprietaires.

C'est la seule source publique qui approche une notion de volume sur Steam,
Valve ne publiant jamais d'unites. La methodologie repose sur un
echantillonnage de profils publics : la marge d'erreur est large et les
fourchettes sont grossieres (« 1 000 000 .. 2 000 000 »). On stocke donc le
milieu de fourchette, en marquant clairement la donnee comme estimee, et on
conserve la fourchette brute dans `extra` pour ne rien perdre.

A ne jamais additionner avec un chiffre officiel dans un meme total.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from ..http import FetchError
from .base import (
    ESTIMATED,
    OWNERS_EST,
    STEAM,
    Collector,
    CollectorResult,
    Observation,
    register,
)

log = logging.getLogger(__name__)

API_URL = "https://steamspy.com/api.php"
RANGE_RE = re.compile(r"([\d\s,]+)\s*\.\.\s*([\d\s,]+)")


def parse_owners(text: str) -> tuple[float | None, str | None]:
    """'1,000,000 .. 2,000,000' -> (1500000.0, texte brut)."""
    if not text:
        return None, None
    match = RANGE_RE.search(text)
    if not match:
        digits = re.sub(r"[^0-9]", "", text)
        return (float(digits) if digits else None), text
    low = re.sub(r"[^0-9]", "", match.group(1))
    high = re.sub(r"[^0-9]", "", match.group(2))
    if not low or not high:
        return None, text
    return (int(low) + int(high)) / 2, text


@register
class SteamSpyCollector(Collector):
    name = "steamspy"
    label = "SteamSpy - proprietaires estimes (estimation tierce)"
    domain = STEAM
    source_url = "https://steamspy.com/"
    provides = "Proprietaires estimes et joueurs recents, top 100 mondial des 2 dernieres semaines"
    reliability = ESTIMATED

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        try:
            data = self.client.get_json(API_URL, params={"request": "top100in2weeks"})
        except FetchError as exc:
            result.notes.append(str(exc))
            return result

        if not isinstance(data, dict) or not data:
            result.notes.append("Reponse SteamSpy vide ou inattendue.")
            return result

        for rank, (appid, entry) in enumerate(data.items(), start=1):
            if not isinstance(entry, dict):
                continue
            title = entry.get("name")
            owners, raw = parse_owners(str(entry.get("owners", "")))
            if not title or owners is None:
                continue
            result.observations.append(
                Observation(
                    source=self.name,
                    domain=STEAM,
                    market="WW",
                    period_type="snapshot",
                    period_start=today,
                    period_end=today,
                    title=title,
                    entity_id=str(appid),
                    platform="Steam",
                    metric=OWNERS_EST,
                    value=float(owners),
                    rank=rank,
                    reliability=ESTIMATED,
                    extra={
                        "owners_range": raw,
                        "average_forever_min": entry.get("average_forever"),
                        "ccu": entry.get("ccu"),
                        "price_cents": entry.get("price"),
                        "developer": entry.get("developer"),
                        "publisher": entry.get("publisher"),
                    },
                )
            )
        return result
