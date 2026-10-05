"""Veille editoriale sur les ventes toutes plateformes.

Pourquoi ce collecteur existe : il n'y a **aucune** source libre publiant des
unites hebdomadaires toutes plateformes.

* VGChartz avait des classements hebdomadaires par pays, mais ils s'arretent
  a la semaine du 29 decembre 2018.
* Les vrais panels sont payants : GSD/GfK pour l'Europe (dont France,
  Allemagne, Royaume-Uni), Circana (ex-NPD) pour les Etats-Unis, Famitsu pour
  le Japon.

Ces chiffres finissent toutefois reprises par la presse specialisee chaque
semaine. Ce collecteur surveille donc les flux RSS et remonte les articles de
classement, avec leur lien : le rapport donne l'information et sa source,
sans inventer de chiffre. Les vrais volumes, si on y a acces, entrent par
`infoshebdo import-csv` (voir collectors/manual.py).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from ..http import FetchError
from .base import GAMES_ALL, THIRD_PARTY, Collector, CollectorResult, register

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")


def _text(node, *names: str) -> str:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _parse_date(raw: str) -> str | None:
    """RSS (RFC 822) ou Atom (ISO 8601) -> date ISO, ou None."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        cleaned = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def parse_feed(xml_text: str) -> list[dict]:
    """Extrait (titre, lien, date, resume) d'un flux RSS 2.0 ou Atom."""
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8", "replace"))
    except ElementTree.ParseError as exc:
        raise FetchError(f"flux illisible : {exc}") from exc

    items: list[dict] = []

    for item in root.iter("item"):  # RSS 2.0
        items.append(
            {
                "title": _text(item, "title"),
                "url": _text(item, "link", "guid"),
                "published_at": _parse_date(_text(item, "pubDate")),
                "summary": TAG_RE.sub("", _text(item, "description"))[:400] or None,
            }
        )

    for entry in root.iter(f"{ATOM}entry"):  # Atom
        link = entry.find(f"{ATOM}link")
        items.append(
            {
                "title": _text(entry, f"{ATOM}title"),
                "url": (link.get("href") if link is not None else "") or "",
                "published_at": _parse_date(
                    _text(entry, f"{ATOM}updated", f"{ATOM}published")
                ),
                "summary": TAG_RE.sub(
                    "", _text(entry, f"{ATOM}summary", f"{ATOM}content")
                )[:400]
                or None,
            }
        )

    return [i for i in items if i["title"] and i["url"]]


def matches(item: dict, keywords: list[str]) -> bool:
    """Un article est retenu si un mot-cle apparait dans le titre ou le resume."""
    if not keywords:
        return True
    haystack = f"{item['title']} {item.get('summary') or ''}".lower()
    return any(kw.lower() in haystack for kw in keywords)


@register
class EditorialWatchCollector(Collector):
    name = "editorial_watch"
    label = "Veille presse - classements toutes plateformes"
    domain = GAMES_ALL
    source_url = "flux RSS configures dans config.yaml"
    provides = (
        "Articles de classement (GSD Europe, Circana US, Famitsu Japon) reperes "
        "par mots-cles ; liens seulement, pas de chiffres"
    )
    reliability = THIRD_PARTY

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        block = self.config.games.get("all_platforms", {}) or {}
        if not block.get("editorial_watch", False):
            result.notes.append("Veille editoriale desactivee.")
            return result

        feeds = block.get("feeds") or []
        if not feeds:
            result.notes.append("Aucun flux configure (games.all_platforms.feeds).")
            return result

        for feed in feeds:
            url = feed.get("url")
            name = feed.get("name") or url
            if not url:
                continue
            try:
                xml_text = self.client.get_text(url)
                items = parse_feed(xml_text)
            except FetchError as exc:
                result.notes.append(f"{name} : {exc}")
                continue

            keywords = feed.get("keywords") or []
            markets = feed.get("markets") or []
            kept = 0
            for item in items:
                if not matches(item, keywords):
                    continue
                result.news.append(
                    {
                        "source": name,
                        "title": item["title"],
                        "url": item["url"],
                        "published_at": item["published_at"],
                        "summary": item["summary"],
                        "markets": markets,
                        "domain": GAMES_ALL,
                    }
                )
                kept += 1
            result.notes.append(f"{name} : {kept} article(s) retenu(s) sur {len(items)}.")
        return result
