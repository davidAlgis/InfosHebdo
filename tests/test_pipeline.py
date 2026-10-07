"""Tests du stockage, de l'agregation et du rendu, sur une base temporaire.

Aucun acces reseau : les collecteurs sont remplaces par des faux qui rendent
des observations construites a la main.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from infoshebdo import db, derive, report as report_module
from infoshebdo.analysis import build_report, fmt_int, fmt_money, rank_move
from infoshebdo.collectors.base import (
    ADMISSIONS,
    BOX_OFFICE,
    GROSS_USD,
    OFFICIAL,
    PARTIAL,
    RANK,
    STEAM,
    Observation,
)
from infoshebdo.config import Config, DEFAULTS


def make_config(**overrides) -> Config:
    raw = {**DEFAULTS, **overrides}
    return Config(raw=raw)


def gross(market: str, title: str, value: float, day: date, rank: int) -> Observation:
    return Observation(
        source="boxofficemojo",
        domain=BOX_OFFICE,
        market=market,
        period_type="weekend",
        period_start=day,
        period_end=day,
        title=title,
        metric=GROSS_USD,
        value=value,
        rank=rank,
        currency="USD",
        reliability=OFFICIAL if market == "US" else PARTIAL,
    )


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "test.sqlite3"
        db.init(self.path)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self._dir.cleanup()


class TestStorage(TempDbCase):
    def test_save_and_read(self):
        db.save_observations(
            self.conn, [gross("US", "Film A", 1000.0, date(2026, 8, 14), 1)]
        )
        self.conn.commit()
        rows = db.chart(self.conn, BOX_OFFICE, "US", GROSS_USD, "2026-08-14")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 1000.0)

    def test_upsert_is_idempotent(self):
        obs = gross("US", "Film A", 1000.0, date(2026, 8, 14), 1)
        for _ in range(3):
            db.save_observations(self.conn, [obs])
        self.conn.commit()
        rows = db.chart(self.conn, BOX_OFFICE, "US", GROSS_USD, "2026-08-14")
        self.assertEqual(len(rows), 1)

    def test_revision_overwrites_value(self):
        # Mojo revise ses estimations : la nouvelle valeur doit gagner.
        db.save_observations(
            self.conn, [gross("US", "Film A", 1000.0, date(2026, 8, 14), 1)]
        )
        db.save_observations(
            self.conn, [gross("US", "Film A", 1234.0, date(2026, 8, 14), 1)]
        )
        self.conn.commit()
        rows = db.chart(self.conn, BOX_OFFICE, "US", GROSS_USD, "2026-08-14")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 1234.0)

    def test_same_title_two_markets_coexist(self):
        db.save_observations(
            self.conn,
            [
                gross("US", "Film A", 1000.0, date(2026, 8, 14), 1),
                gross("JP", "Film A", 500.0, date(2026, 8, 14), 1),
            ],
        )
        self.conn.commit()
        self.assertEqual(
            len(db.chart(self.conn, BOX_OFFICE, "US", GROSS_USD, "2026-08-14")), 1
        )
        self.assertEqual(
            len(db.chart(self.conn, BOX_OFFICE, "JP", GROSS_USD, "2026-08-14")), 1
        )

    def test_news_deduplicated_by_url(self):
        item = {
            "source": "Gematsu",
            "title": "Famitsu sales",
            "url": "https://example.com/a",
        }
        db.save_news(self.conn, [item])
        db.save_news(self.conn, [item])
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0], 1)


class TestWorldwideAggregate(TempDbCase):
    def setUp(self):
        super().setUp()
        db.save_observations(
            self.conn,
            [
                gross("US", "Film A", 1000.0, date(2026, 8, 14), 1),
                gross("JP", "Film A", 250.0, date(2026, 8, 14), 2),
                gross("US", "Film B", 800.0, date(2026, 8, 14), 2),
            ],
        )
        self.conn.commit()

    def test_sums_across_markets(self):
        observations = derive.worldwide_box_office(self.conn, ["US", "JP", "WW"])
        by_title = {o.title: o for o in observations}
        self.assertEqual(by_title["Film A"].value, 1250.0)
        self.assertEqual(by_title["Film B"].value, 800.0)

    def test_ranks_by_total(self):
        observations = derive.worldwide_box_office(self.conn, ["US", "JP", "WW"])
        ordered = sorted(observations, key=lambda o: o.rank)
        self.assertEqual(ordered[0].title, "Film A")

    def test_coverage_flagged_when_a_market_is_missing(self):
        observations = derive.worldwide_box_office(self.conn, ["US", "JP", "WW"])
        by_title = {o.title: o for o in observations}
        self.assertEqual(by_title["Film A"].extra["coverage"], "complete")
        # Film B n'a que les Etats-Unis : la couverture doit etre signalee.
        self.assertEqual(by_title["Film B"].extra["coverage"], "partielle")
        self.assertEqual(by_title["Film B"].extra["markets_included"], ["US"])

    def test_aggregate_is_never_reaggregated(self):
        # Deux passages ne doivent pas doubler les totaux.
        db.save_observations(
            self.conn, derive.worldwide_box_office(self.conn, ["US", "JP", "WW"])
        )
        self.conn.commit()
        again = derive.worldwide_box_office(self.conn, ["US", "JP", "WW"])
        self.assertEqual({o.title: o.value for o in again}["Film A"], 1250.0)


class TestTitleResolution(TempDbCase):
    def test_appid_replaced_by_known_name(self):
        db.save_observations(
            self.conn,
            [
                Observation(
                    source="steam_topsellers",
                    domain=STEAM,
                    market="WW",
                    period_type="week",
                    period_start=date(2026, 8, 11),
                    title="Counter-Strike 2",
                    entity_id="730",
                    platform="Steam",
                    metric=RANK,
                    value=1.0,
                    rank=1,
                    reliability=OFFICIAL,
                ),
                Observation(
                    source="steam_concurrents",
                    domain=STEAM,
                    market="WW",
                    period_type="snapshot",
                    period_start=date(2026, 8, 21),
                    title="appid 730",
                    entity_id="730",
                    platform="Steam",
                    metric="concurrent_players",
                    value=1_000_000.0,
                    rank=1,
                    reliability=OFFICIAL,
                ),
            ],
        )
        self.conn.commit()
        fixed = derive.resolve_titles(self.conn)
        self.assertEqual(fixed, 1)
        titles = {
            r["title"]
            for r in self.conn.execute(
                "SELECT title FROM observations WHERE metric = 'concurrent_players'"
            )
        }
        self.assertEqual(titles, {"Counter-Strike 2"})


class TestFormatting(unittest.TestCase):
    def test_int_grouping(self):
        self.assertEqual(fmt_int(1189232).replace(" ", " "), "1 189 232")

    def test_money_millions(self):
        self.assertEqual(
            fmt_money(70711990, "USD").replace(" ", " "), "$70,7 M"
        )

    def test_money_small(self):
        self.assertEqual(fmt_money(766000, "USD").replace(" ", " "), "$766 000")

    def test_rank_move(self):
        self.assertEqual(rank_move(1, 5)[1], "up")
        self.assertEqual(rank_move(5, 1)[1], "down")
        self.assertEqual(rank_move(3, 3)[0], "=")
        self.assertEqual(rank_move(3, None)[0], "nouveau")

    def test_no_previous_period_means_no_movement(self):
        # Sans periode de reference, ne rien afficher plutot que "nouveau".
        self.assertEqual(rank_move(3, None, comparable=False)[0], "")


class TestReportRendering(TempDbCase):
    def setUp(self):
        super().setUp()
        db.save_observations(
            self.conn,
            [
                gross("US", "Film A", 1000.0, date(2026, 8, 7), 1),
                gross("US", "Film A", 700.0, date(2026, 8, 14), 1),
                Observation(
                    source="allocine_france",
                    domain=BOX_OFFICE,
                    market="FR",
                    period_type="week",
                    period_start=date(2026, 8, 12),
                    period_end=date(2026, 8, 18),
                    title="Film Francais",
                    metric=ADMISSIONS,
                    value=1189232.0,
                    rank=1,
                    reliability=OFFICIAL,
                ),
            ],
        )
        self.conn.commit()
        self.conn.close()

    def _report(self):
        import infoshebdo.paths as paths_module

        original = paths_module.db_path
        paths_module.db_path = lambda: self.path
        try:
            config = make_config(
                box_office={**DEFAULTS["box_office"], "markets": ["FR", "US"]},
                games={**DEFAULTS["games"], "enabled": False},
            )
            return build_report(config, today=date(2026, 8, 21))
        finally:
            paths_module.db_path = original

    def test_report_has_tables_and_deltas(self):
        report = self._report()
        tables = {t.key: t for s in report.sections for t in s.tables}
        self.assertIn("bo_us", tables)
        self.assertIn("bo_fr", tables)
        # 1000 -> 700 la semaine suivante : -30 %.
        self.assertEqual(tables["bo_us"].rows[0].delta_pct, "-30%")
        self.assertEqual(tables["bo_us"].rows[0].delta_class, "down")

    def test_html_and_text_render(self):
        report = self._report()
        html = report_module.render_html(report)
        text = report_module.render_text(report)
        self.assertIn("Film Francais", html)
        self.assertIn("Film Francais", text)
        self.assertNotIn("{{", html)
        self.assertIn("Box-office", text)

    def test_france_is_the_only_panel_open_and_each_ranking_has_its_source(self):
        # Sur de vraies donnees : seul le box-office France est deplie, et les
        # liens visent la semaine exacte de chaque classement.
        report = self._report()
        tables = {t.key: t for s in report.sections for t in s.tables}
        self.assertTrue(tables["bo_fr"].expanded)
        self.assertFalse(tables["bo_us"].expanded)
        self.assertEqual(
            tables["bo_fr"].links[0].url,
            "https://www.allocine.fr/boxoffice/france/sem-2026-08-12/",
        )
        self.assertEqual(
            tables["bo_us"].links[0].url,
            "https://www.boxofficemojo.com/weekend/2026W33/",
        )

    def test_html_is_a_standalone_utf8_page(self):
        # Le rapport s'ouvre directement depuis le disque (file://). Sans
        # doctype ni charset, le navigateur devine l'encodage et affiche
        # « Odyssée » en « OdyssÃ©e ».
        html = report_module.render_html(self._report())
        self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn("</html>", html)
        self.assertEqual(html.lower().count("<title>"), 1)

    def test_html_has_no_external_dependency(self):
        # Le fichier doit s'ouvrir hors ligne et se deplacer sans rien perdre.
        html = report_module.render_html(self._report())
        for forbidden in ("<script", "<link", "http://", "src="):
            self.assertNotIn(forbidden, html)

    def test_headline_mentions_leaders(self):
        report = self._report()
        self.assertIn("Film Francais", report.headline)


if __name__ == "__main__":
    unittest.main()
