"""Tests d'ecriture de la configuration.

L'interface graphique reecrit config.yaml a chaque enregistrement.
Un rendu qui perd une valeur, ou qui produit un YAML invalide, effacerait
silencieusement des reglages : c'est ce que ces tests verrouillent.
"""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from infoshebdo import config as config_module
from infoshebdo.config import DEFAULTS, _deep_merge, render_yaml


def sample() -> dict:
    raw = copy.deepcopy(DEFAULTS)
    raw["games"]["all_platforms"]["feeds"] = [
        {
            "name": "Gematsu",
            "url": "https://www.gematsu.com/feed",
            "markets": ["JP"],
            "keywords": ["famitsu", "hardware sales"],
        }
    ]
    return raw


class TestYamlRoundTrip(unittest.TestCase):
    def _round_trip(self, raw: dict) -> dict:
        return _deep_merge(DEFAULTS, yaml.safe_load(render_yaml(raw)) or {})

    def test_defaults_survive(self):
        raw = sample()
        self.assertEqual(self._round_trip(raw), _deep_merge(DEFAULTS, raw))

    def test_modified_values_survive(self):
        raw = sample()
        raw["top_n"] = 25
        raw["backfill_weeks"] = 3
        raw["box_office"]["markets"] = ["FR", "GB"]
        raw["games"]["steam_markets"] = ["WW", "IT", "ES"]
        raw["games"]["sources"]["steamspy"] = False
        raw["box_office"]["enabled"] = False
        self.assertEqual(self._round_trip(raw), _deep_merge(DEFAULTS, raw))

    def test_feeds_survive(self):
        raw = sample()
        got = self._round_trip(raw)
        feeds = got["games"]["all_platforms"]["feeds"]
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]["keywords"], ["famitsu", "hardware sales"])

    def test_empty_feed_list(self):
        raw = sample()
        raw["games"]["all_platforms"]["feeds"] = []
        self.assertEqual(self._round_trip(raw)["games"]["all_platforms"]["feeds"], [])

    def test_auto_open_survives(self):
        raw = sample()
        raw["report"]["auto_open"] = False
        self.assertFalse(self._round_trip(raw)["report"]["auto_open"])

    def test_empty_mojo_area_stays_empty_string(self):
        # La zone vide est la zone par defaut du site : elle ne doit pas
        # devenir None, sinon le collecteur ajouterait un parametre ?area=None.
        raw = sample()
        got = self._round_trip(raw)
        self.assertEqual(got["box_office"]["mojo_areas"]["US"], "")

    def test_accents_and_quotes_survive(self):
        raw = sample()
        raw["report"]["title"] = 'Veille "cinéma" & jeu vidéo'
        self.assertEqual(
            self._round_trip(raw)["report"]["title"], raw["report"]["title"]
        )

    def test_output_is_commented(self):
        self.assertIn("# InfosHebdo", render_yaml(sample()))

    def test_save_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            config_module.save(sample(), target)
            self.assertTrue(target.exists())
            reread = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual(reread["top_n"], DEFAULTS["top_n"])


LEGACY_YAML = """
top_n: 12
report:
  title: Mon titre
  attach_html: true
  open_after_send: true
  open_on_ui_start: true
email:
  enabled: true
  subject: "[InfosHebdo] Semaine {week}"
"""


class TestLegacyConfig(unittest.TestCase):
    """Un config.yaml de l'ancienne version (envoi par courriel) doit charger
    sans erreur, garder ses reglages utiles et pouvoir etre reecrit."""

    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            target.write_text(text, encoding="utf-8")
            return config_module.load(target)

    def test_legacy_keys_are_dropped(self):
        cfg = self._load(LEGACY_YAML)
        self.assertNotIn("email", cfg.raw)
        for key in ("attach_html", "open_after_send", "open_on_ui_start"):
            self.assertNotIn(key, cfg.raw["report"])

    def test_useful_settings_are_kept(self):
        cfg = self._load(LEGACY_YAML)
        self.assertEqual(cfg.top_n, 12)
        self.assertEqual(cfg.report["title"], "Mon titre")
        self.assertTrue(cfg.report["auto_open"])

    def test_legacy_config_can_be_saved_back(self):
        # Sans le nettoyage, `save` refuserait : le rendu ne connait plus ces
        # cles, donc ne se relirait pas a l'identique.
        cfg = self._load(LEGACY_YAML)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            config_module.save(cfg.raw, target)
            text = target.read_text(encoding="utf-8")
        self.assertNotIn("email", text)
        self.assertNotIn("smtp", text.lower())


if __name__ == "__main__":
    unittest.main()
