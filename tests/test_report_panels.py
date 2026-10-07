"""Tests des panneaux pliables et des liens de source du rapport.

Chaque classement est un panneau (`<details>`, natif : ni script ni feuille
externe). Tous sont plies a l'ouverture, sauf le box-office France. Chaque
classement renvoie vers la page qui publie ses chiffres, sur la semaine exacte
quand on la connait : verifier un chiffre doit demander un clic, pas une
recherche.
"""
from __future__ import annotations

import unittest
from datetime import date
from html.parser import HTMLParser

from infoshebdo import report as report_module, sources
from infoshebdo.analysis import Report, Row, Section, Table
from infoshebdo.collectors import allocine, boxofficemojo
from infoshebdo.sources import Link


class PanelParser(HTMLParser):
    """Releve, pour chaque `<details>`, son titre, s'il est ouvert et ses liens."""

    def __init__(self) -> None:
        super().__init__()
        self.panels: list[dict] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "details":
            self.panels.append({"open": "open" in attributes, "title": "", "links": []})
        elif tag == "span" and self.panels and "font-size:15px" in (attributes.get("style") or ""):
            self._in_title = True
        elif tag == "a" and self.panels and attributes.get("href", "").startswith("https://"):
            self.panels[-1]["links"].append(attributes)

    def handle_endtag(self, tag):
        if tag == "span":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.panels:
            self.panels[-1]["title"] += data


def panels_of(html: str) -> list[dict]:
    parser = PanelParser()
    parser.feed(html)
    return parser.panels


def make_report(*tables: Table, notices=()) -> Report:
    return Report(
        title="Veille", generated_at="7 octobre 2026", week_label="semaine 40",
        period_start=date(2026, 9, 28), period_end=date(2026, 10, 4),
        sections=[Section(key="s", title="Section", tables=list(tables))],
        notices=list(notices),
    )


def table(key: str, title: str, *, rows: bool = True, links=(), expanded: bool = False) -> Table:
    return Table(
        key=key, title=title, unit="u", source_label="Source", reliability="officiel",
        period_label="2026W40",
        rows=[Row(rank=1, title="Film A", value_fmt="1 000")] if rows else [],
        links=list(links), expanded=expanded,
    )


class TestPanels(unittest.TestCase):
    def render(self, *tables: Table) -> list[dict]:
        return panels_of(report_module.render_html(make_report(*tables)))

    def test_every_ranking_is_a_panel(self):
        panels = self.render(table("a", "Un"), table("b", "Deux"), table("c", "Trois"))
        self.assertEqual([p["title"] for p in panels], ["Un", "Deux", "Trois"])

    def test_panels_are_folded_unless_marked_expanded(self):
        panels = self.render(
            table("bo_fr", "France", expanded=True), table("bo_us", "Etats-Unis"), table("x", "Autre")
        )
        self.assertEqual([p["open"] for p in panels], [True, False, False])

    def test_an_empty_ranking_is_a_folded_panel_too(self):
        panels = self.render(table("bo_jp", "Japon", rows=False))
        self.assertEqual(len(panels), 1)
        self.assertFalse(panels[0]["open"])

    def test_folded_panel_still_shows_the_leader(self):
        html = report_module.render_html(make_report(table("a", "Un")))
        self.assertIn("1. Film A", html)          # visible sans deplier

    def test_no_script_and_no_external_resource(self):
        html = report_module.render_html(make_report(table("a", "Un")))
        for forbidden in ("<script", "<link", "http://", " src="):
            self.assertNotIn(forbidden, html)


class TestSourceLinks(unittest.TestCase):
    def test_each_link_opens_in_a_new_tab_safely(self):
        link = Link("Allocine", "https://www.allocine.fr/x")
        (panel,) = panels_of(report_module.render_html(make_report(table("a", "Un", links=[link]))))
        (anchor,) = panel["links"]
        self.assertEqual(anchor["href"], link.url)
        self.assertEqual(anchor["target"], "_blank")
        self.assertIn("noopener", anchor["rel"])

    def test_several_sources_are_all_listed(self):
        links = [Link("A", "https://a.test/"), Link("B", "https://b.test/")]
        html = report_module.render_html(make_report(table("a", "Un", links=links)))
        self.assertIn("Sources :", html)
        self.assertIn("https://a.test/", html)
        self.assertIn("https://b.test/", html)

    def test_a_single_source_is_singular(self):
        html = report_module.render_html(
            make_report(table("a", "Un", links=[Link("A", "https://a.test/")]))
        )
        self.assertIn("Source :", html)
        self.assertNotIn("Sources :", html)

    def test_no_link_no_source_line(self):
        html = report_module.render_html(make_report(table("a", "Un")))
        self.assertNotIn("Source :", html)

    def test_the_text_version_lists_the_urls(self):
        text = report_module.render_text(
            make_report(table("a", "Un", links=[Link("Allocine", "https://www.allocine.fr/x")]))
        )
        self.assertIn("Source : Allocine", text)
        self.assertIn("https://www.allocine.fr/x", text)

    def test_the_text_version_lists_urls_of_empty_rankings_too(self):
        text = report_module.render_text(
            make_report(
                table("a", "Un", rows=False, links=[Link("Steam", "https://store.steampowered.com/x")])
            )
        )
        self.assertIn("https://store.steampowered.com/x", text)

    def test_a_hostile_link_label_is_escaped(self):
        link = Link("<img src=x onerror=alert(1)>", "https://a.test/")
        html = report_module.render_html(make_report(table("a", "Un", links=[link])))
        self.assertNotIn("<img", html)


class TestSourceAddresses(unittest.TestCase):
    def test_allocine_points_at_the_exact_week(self):
        link = sources.allocine_week("2026-09-23")
        self.assertEqual(link.url, "https://www.allocine.fr/boxoffice/france/sem-2026-09-23/")
        self.assertIn("23/09/2026", link.label)

    def test_allocine_falls_back_to_the_general_page(self):
        self.assertEqual(sources.allocine_week(None).url, sources.ALLOCINE_HOME)

    def test_the_week_page_is_the_one_the_collector_downloads(self):
        # Le lien du rapport et ce que la collecte a lu ne doivent pas diverger.
        self.assertEqual(
            sources.allocine_week("2026-09-23").url,
            allocine.WEEK_URL.format(day="2026-09-23"),
        )
        self.assertEqual(
            sources.mojo_week("2026-10-02", "", "x").url,
            boxofficemojo.BASE_URL.format(year=2026, week=40),
        )

    def test_mojo_default_zone_has_no_parameter(self):
        self.assertEqual(
            sources.mojo_week("2026-10-02", "", "Box Office Mojo").url,
            "https://www.boxofficemojo.com/weekend/2026W40/",
        )

    def test_mojo_label_shows_the_week(self):
        self.assertEqual(
            sources.mojo_week("2026-08-14", "", "Box Office Mojo - Etats-Unis").label,
            "Box Office Mojo - Etats-Unis (2026W33)",
        )

    def test_mojo_foreign_zone_adds_the_area(self):
        self.assertEqual(
            sources.mojo_week("2026-10-02", "JP", "x").url,
            "https://www.boxofficemojo.com/weekend/2026W40/?area=JP",
        )

    def test_mojo_unreadable_week_falls_back_to_the_zone_page(self):
        for unreadable in (None, "", "semaine 40", "2026W40", "pas une date"):
            self.assertEqual(
                sources.mojo_week(unreadable, "FR", "x").url,
                "https://www.boxofficemojo.com/weekend/?area=FR",
            )

    def test_steam_world_ranking_is_global(self):
        self.assertTrue(sources.steam_topsellers("WW").url.endswith("/topselling/global"))
        self.assertTrue(sources.steam_topsellers("FR").url.endswith("/topselling/FR"))

    def test_every_address_is_https(self):
        for link in (
            sources.allocine_week("2026-09-23"),
            sources.mojo_week("2026-10-02", "JP", "x"),
            sources.steam_topsellers("US"),
            sources.steam_most_played(),
            sources.steamspy(),
        ):
            self.assertTrue(link.url.startswith("https://"), link)


if __name__ == "__main__":
    unittest.main()
