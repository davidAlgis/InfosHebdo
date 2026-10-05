"""Client HTTP partage : un seul endroit pour l'entete, les reessais et le
delai de politesse entre deux requetes vers un meme hote.

Un cache disque optionnel (INFOSHEBDO_HTTP_CACHE=1) evite de retelecharger
les memes pages pendant les mises au point de parsers.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

import requests

from . import paths

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25
MAX_ATTEMPTS = 3

# Serveurs distincts injoignables, sans la moindre reponse obtenue, a partir
# desquels on conclut que c'est le poste qui est hors ligne : inutile alors
# d'essayer (trois tentatives et des pauses) chacun des suivants.
OFFLINE_AFTER_HOSTS = 3


class FetchError(RuntimeError):
    """Echec de recuperation apres reessais : la source est consideree KO."""


class Client:
    def __init__(self, delay: float = 1.0, use_cache: bool | None = None) -> None:
        self.delay = max(0.0, delay)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            }
        )
        if use_cache is None:
            use_cache = os.environ.get("INFOSHEBDO_HTTP_CACHE", "") == "1"
        self.use_cache = use_cache
        self._last_call: dict[str, float] = {}
        # Hotes injoignables (connexion refusee, delai depasse), avec la cause.
        # Un client ne vit que le temps d'une collecte : hors ligne, chaque
        # source reessaierait sinon chacune de ses semaines (trois tentatives
        # et des pauses a chaque fois), et l'erreur n'apparaitrait qu'apres
        # de longues minutes.
        self._unreachable: dict[str, str] = {}
        # Bilan des requetes de ce client. Les collecteurs avalent les echecs
        # reseau dans de simples notes (une semaine manquante est normale), si
        # bien qu'une collecte hors ligne ressemble a une collecte « vide ».
        # Le pipeline lit ces compteurs pour distinguer « rien a signaler »
        # de « aucune requete n'a abouti ».
        self.ok = 0
        self.failures: list[str] = []

    # ------------------------------------------------------------------ #
    @staticmethod
    def _host(url: str) -> str:
        return url.split("/")[2] if "://" in url else url

    def _wait(self, url: str) -> None:
        host = self._host(url)
        previous = self._last_call.get(host)
        if previous is not None:
            gap = self.delay - (time.monotonic() - previous)
            if gap > 0:
                time.sleep(gap)
        self._last_call[host] = time.monotonic()

    def _cache_file(self, url: str, params: Any):
        key = hashlib.sha256(f"{url}|{params}".encode("utf-8")).hexdigest()[:32]
        return paths.CACHE_DIR / f"{key}.cache"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _describe(error: Exception | None) -> str:
        """Cause lisible d'un echec reseau, avec le type technique entre parentheses."""
        if isinstance(error, requests.Timeout):
            reason = f"delai depasse apres {DEFAULT_TIMEOUT} s"
        elif isinstance(error, requests.ConnectionError):
            reason = "connexion impossible (poste hors ligne, pare-feu ou serveur indisponible)"
        else:
            reason = str(error)[:200]
        return f"{reason} ({type(error).__name__})"

    def get_text(self, url: str, params: dict | None = None) -> str:
        try:
            text = self._fetch(url, params)
        except FetchError as exc:
            self.failures.append(str(exc))
            raise
        self.ok += 1
        return text

    def _fetch(self, url: str, params: dict | None = None) -> str:
        if self.use_cache:
            paths.ensure_dirs()
            cached = self._cache_file(url, params)
            if cached.exists():
                return cached.read_text(encoding="utf-8")

        host = self._host(url)
        if host in self._unreachable:
            raise FetchError(
                f"{url} injoignable : {host} ne repond pas "
                f"({self._unreachable[host]}), abandonne pour cette collecte"
            )

        if self.ok == 0 and len(self._unreachable) >= OFFLINE_AFTER_HOSTS:
            raise FetchError(
                f"{url} injoignable : {len(self._unreachable)} serveurs ne repondent pas "
                "et aucun n'a repondu, le poste est probablement hors ligne, "
                "abandonne pour cette collecte"
            )

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait(url)
            try:
                resp = self.session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
                if resp.status_code == 404:
                    raise FetchError(f"404 {resp.url}")
                if resp.status_code in (429, 503):
                    raise requests.RequestException(f"{resp.status_code} {resp.url}")
                resp.raise_for_status()
                resp.encoding = resp.encoding or "utf-8"
                text = resp.text
                if self.use_cache:
                    self._cache_file(url, params).write_text(text, encoding="utf-8")
                return text
            except FetchError:
                raise
            except requests.RequestException as exc:
                last_error = exc
                log.debug("tentative %s/%s echouee sur %s : %s", attempt, MAX_ATTEMPTS, url, exc)
                if attempt < MAX_ATTEMPTS:
                    time.sleep(1.5 * attempt)
        if isinstance(last_error, (requests.ConnectionError, requests.Timeout)):
            self._unreachable[host] = type(last_error).__name__
        raise FetchError(f"{url} injoignable : {self._describe(last_error)}")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        import json

        text = self.get_text(url, params)
        try:
            return json.loads(text)
        except ValueError as exc:
            # Le texte est arrive mais ne sert a rien : ce n'est pas un succes.
            self.ok -= 1
            message = f"reponse non-JSON depuis {url} : {exc}"
            self.failures.append(message)
            raise FetchError(message) from exc
