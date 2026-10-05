"""Chargement de config.yaml et des variables d'environnement (.env).

La configuration reste utilisable sans fichier : les valeurs par defaut
ci-dessous suffisent a faire tourner une collecte complete.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from . import paths

DEFAULTS: dict[str, Any] = {
    "top_n": 10,
    "backfill_weeks": 8,
    "timezone": "Europe/Paris",
    "box_office": {
        "enabled": True,
        "markets": ["FR", "US", "JP", "WW"],
        "sources": {"allocine_france": True, "boxofficemojo": True},
        "mojo_areas": {"US": "", "FR": "FR", "JP": "JP"},
    },
    "games": {
        "enabled": True,
        "steam_markets": ["WW", "FR", "US", "JP", "GB", "DE"],
        "sources": {
            "steam_topsellers": True,
            "steam_concurrents": True,
            "steamspy": True,
        },
        "all_platforms": {
            "editorial_watch": True,
            "manual_import": True,
            "feeds": [],
        },
    },
    "report": {
        "title": "Veille hebdo - Cinema & Jeu video",
        "empty_sections": "show",
        # Ouverture automatique du rapport, une fois par semaine.
        "auto_open": True,
    },
    "interface": {
        "tray": True,
        "minimize_after_first_use": True,
        "close_to_tray": True,
    },
}

# Cles d'anciennes versions (envoi par courriel). On les retire au chargement :
# `save` verifie que le rendu se relit a l'identique, et une cle que le rendu
# ne connait plus ferait refuser tout enregistrement.
LEGACY_KEYS: dict[str, tuple[str, ...]] = {
    "": ("email",),
    "report": ("attach_html", "open_after_send", "open_on_ui_start"),
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusion recursive : l'utilisateur ne redefinit que ce qu'il change."""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    raw: dict[str, Any]

    # --- acces confort ---
    @property
    def top_n(self) -> int:
        return int(self.raw["top_n"])

    @property
    def backfill_weeks(self) -> int:
        return max(1, int(self.raw["backfill_weeks"]))

    @property
    def timezone(self) -> str:
        return str(self.raw["timezone"])

    @property
    def box_office(self) -> dict:
        return self.raw["box_office"]

    @property
    def games(self) -> dict:
        return self.raw["games"]

    @property
    def report(self) -> dict:
        return self.raw["report"]

    @property
    def interface(self) -> dict:
        return self.raw["interface"]

    def source_enabled(self, section: str, name: str) -> bool:
        """True si la source `name` de la section `box_office`/`games` est active."""
        block = self.raw.get(section, {})
        if not block.get("enabled", True):
            return False
        return bool(block.get("sources", {}).get(name, False))

    @property
    def http_delay(self) -> float:
        try:
            return float(os.environ.get("INFOSHEBDO_HTTP_DELAY", "1.0"))
        except ValueError:
            return 1.0


def _drop_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    for section, keys in LEGACY_KEYS.items():
        block = raw if not section else raw.get(section)
        if isinstance(block, dict):
            for key in keys:
                block.pop(key, None)
    return raw


def load(config_file: Path | None = None) -> Config:
    """Charge .env puis config.yaml, fusionnes sur les valeurs par defaut."""
    if paths.ENV_FILE.exists():
        load_dotenv(paths.ENV_FILE, override=True)
    else:
        load_dotenv()

    path = config_file or paths.CONFIG_FILE
    user_cfg: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
    return Config(raw=_drop_legacy(_deep_merge(DEFAULTS, user_cfg)))


# --------------------------------------------------------------------------- #
# Ecriture
#
# L'interface Tkinter doit pouvoir reecrire config.yaml sans que le fichier
# devienne illisible a la main. PyYAML ne sait pas conserver les commentaires
# d'un fichier existant : on regenere donc le fichier entier a partir d'un
# gabarit qui porte les commentaires. Les deux chemins de configuration
# (fichier edite a la main, interface graphique) restent ainsi equivalents.
# --------------------------------------------------------------------------- #
def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(c in text for c in ":#{}[]&*!|>'\"%@`,") or text != text.strip():
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _flow_list(values: Any) -> str:
    items = values or []
    return "[" + ", ".join(_scalar(v) for v in items) + "]"


def render_yaml(raw: dict[str, Any]) -> str:
    """Rend config.yaml, commentaires compris, a partir des valeurs courantes."""
    box = raw.get("box_office", {})
    games = raw.get("games", {})
    platforms = games.get("all_platforms", {}) or {}
    report = raw.get("report", {})
    interface = raw.get("interface", {})
    lines: list[str] = []
    out = lines.append

    out("# " + "-" * 73)
    out("# InfosHebdo - configuration de la veille")
    out("#")
    out("# Ce fichier est editable a la main et reecrit par l'interface")
    out("# graphique (infoshebdo ui). Les commentaires sont regeneres a chaque")
    out("# enregistrement : ne pas y ajouter de notes personnelles.")
    out("#")
    out("# Variables facultatives (INFOSHEBDO_DB, INFOSHEBDO_HTTP_DELAY) : .env.")
    out("# " + "-" * 73)
    out("")
    out("# Nombre de lignes conservees par classement dans le rapport.")
    out(f"top_n: {_scalar(raw.get('top_n'))}")
    out("")
    out("# Profondeur de rattrapage : a chaque collecte on re-interroge les N")
    out("# dernieres semaines. Indispensable car le box-office international")
    out("# (France, Japon) est publie avec plusieurs semaines de retard et")
    out("# revise apres coup.")
    out(f"backfill_weeks: {_scalar(raw.get('backfill_weeks'))}")
    out("")
    out("# Fuseau utilise pour decider \"quelle est la semaine ecoulee\".")
    out(f"timezone: {_scalar(raw.get('timezone'))}")
    out("")
    out("box_office:")
    out(f"  enabled: {_scalar(box.get('enabled'))}")
    out("  # Marches suivis. 'WW' est un agregat calcule, pas une source.")
    out(f"  markets: {_flow_list(box.get('markets'))}")
    out("  sources:")
    out("    # entrees France (donnee professionnelle relayee par Allocine)")
    out(f"    allocine_france: {_scalar(box.get('sources', {}).get('allocine_france'))}")
    out("    # recettes USD, zones US / FR / JP")
    out(f"    boxofficemojo: {_scalar(box.get('sources', {}).get('boxofficemojo'))}")
    out("  # Zones Box Office Mojo -> marche interne. La chaine vide est la")
    out("  # zone par defaut du site (Amerique du Nord).")
    out("  mojo_areas:")
    for market, area in (box.get("mojo_areas", {}) or {}).items():
        out(f"    {market}: {_scalar(area)}")
    out("")
    out("games:")
    out(f"  enabled: {_scalar(games.get('enabled'))}")
    out("  # Marches Steam. 'WW' = classement mondial officiel Steam.")
    out(f"  steam_markets: {_flow_list(games.get('steam_markets'))}")
    out("  sources:")
    out("    # classement hebdo officiel Steam, par pays")
    out(f"    steam_topsellers: {_scalar(games.get('sources', {}).get('steam_topsellers'))}")
    out("    # joueurs simultanes (officiel, mondial)")
    out(f"    steam_concurrents: {_scalar(games.get('sources', {}).get('steam_concurrents'))}")
    out("    # proprietaires estimes (estimation tierce)")
    out(f"    steamspy: {_scalar(games.get('sources', {}).get('steamspy'))}")
    out("  # Ventes toutes plateformes : aucune source libre ne publie d'unites")
    out("  # hebdomadaires. On surveille donc les publications de classements.")
    out("  all_platforms:")
    out(f"    editorial_watch: {_scalar(platforms.get('editorial_watch'))}")
    out(f"    manual_import: {_scalar(platforms.get('manual_import'))}")
    feeds = platforms.get("feeds") or []
    if not feeds:
        out("    feeds: []")
    else:
        out("    feeds:")
        for feed in feeds:
            out(f"      - name: {_scalar(feed.get('name'))}")
            out(f"        url: {_scalar(feed.get('url'))}")
            out(f"        markets: {_flow_list(feed.get('markets'))}")
            out(f"        keywords: {_flow_list(feed.get('keywords'))}")
    out("")
    out("report:")
    out(f"  title: {_scalar(report.get('title'))}")
    out("  # Sections vides : 'hide' pour les masquer, 'show' pour afficher")
    out("  # l'absence de donnee (recommande : on voit tout de suite qu'une")
    out("  # source est tombee).")
    out(f"  empty_sections: {_scalar(report.get('empty_sections'))}")
    out("  # Ouvrir le rapport dans le navigateur, une seule fois par semaine : le")
    out("  # premier jour ou la tache planifiee tourne (lundi si le poste est")
    out("  # allume, sinon mardi, etc.).")
    out(f"  auto_open: {_scalar(report.get('auto_open'))}")
    out("")
    out("interface:")
    out("  # Icone dans la zone de notification, pres de l'horloge.")
    out(f"  tray: {_scalar(interface.get('tray'))}")
    out("  # Se replier dans la zone de notification apres le premier")
    out("  # traitement lance dans la session.")
    out(f"  minimize_after_first_use: {_scalar(interface.get('minimize_after_first_use'))}")
    out("  # La croix de fermeture replie au lieu de quitter. « Quitter » reste")
    out("  # accessible par le menu de l'icone.")
    out(f"  close_to_tray: {_scalar(interface.get('close_to_tray'))}")
    out("")
    out("")
    return "\n".join(lines)


def save(raw: dict[str, Any], config_file: Path | None = None) -> Path:
    """Ecrit config.yaml. Verifie que le rendu se relit a l'identique."""
    path = config_file or paths.CONFIG_FILE
    text = render_yaml(raw)

    # Garde-fou : un rendu casse ne doit pas remplacer un fichier valide.
    reread = yaml.safe_load(text) or {}
    merged = _deep_merge(DEFAULTS, reread)
    if merged != _deep_merge(DEFAULTS, raw):
        raise ValueError(
            "le rendu de config.yaml ne se relit pas a l'identique, "
            "enregistrement annule"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


