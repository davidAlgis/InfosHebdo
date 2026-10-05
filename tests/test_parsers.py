"""Tests des parties qui cassent en silence : calendriers et parsers.

Aucun acces reseau ici. Les extraits HTML sont reduits mais reproduisent la
structure reelle des pages observees (classes CSS, attributs data-heading,
espaces insecables dans les nombres).

Lancer : python -m unittest discover -s tests
"""
from __future__ import annotations

import unittest
from datetime import date

from infoshebdo import weeks
from infoshebdo.collectors import allocine, boxofficemojo, editorial, steamspy


class TestMojoWeeks(unittest.TestCase):
    def test_first_friday(self):
        # 1er janvier 2026 est un jeudi : la semaine 1 demarre le 2.
        self.assertEqual(weeks.first_friday(2026), date(2026, 1, 2))

    def test_week_to_dates(self):
        # Verifie contre la page reelle : 2026W33 = week-end du 14 au 16 aout.
        self.assertEqual(
            weeks.mojo_week_to_dates(2026, 33), (date(2026, 8, 14), date(2026, 8, 16))
        )

    def test_latest_complete_skips_ongoing_weekend(self):
        # Vendredi 21 aout : le week-end en cours n'est pas encore exploitable.
        self.assertEqual(weeks.latest_complete_mojo_week(date(2026, 8, 21)), (2026, 33))
        # Lundi 24 aout : le week-end du 21 est termine.
        self.assertEqual(weeks.latest_complete_mojo_week(date(2026, 8, 24)), (2026, 34))

    def test_weeks_back_is_contiguous(self):
        got = weeks.mojo_weeks_back(date(2026, 8, 21), 4)
        self.assertEqual(got, [(2026, 33), (2026, 32), (2026, 31), (2026, 30)])

    def test_year_boundary(self):
        year, week = weeks.mojo_week_of(date(2026, 1, 1))
        self.assertEqual(year, 2025)  # avant le premier vendredi de 2026


class TestFrenchWeeks(unittest.TestCase):
    def test_week_starts_on_wednesday(self):
        for day in (date(2026, 8, 12), date(2026, 8, 15), date(2026, 8, 18)):
            self.assertEqual(weeks.french_week_start(day), date(2026, 8, 12))

    def test_latest_complete(self):
        # Vendredi 21 : la semaine ouverte le 19 est en cours.
        self.assertEqual(
            weeks.latest_complete_french_week(date(2026, 8, 21)), date(2026, 8, 12)
        )

    def test_bounds(self):
        self.assertEqual(
            weeks.french_week_bounds(date(2026, 8, 12)),
            (date(2026, 8, 12), date(2026, 8, 18)),
        )


MOJO_HTML = """
<table><tr>
  <th>Rank</th><th>LW</th><th>Release</th><th>Gross</th><th>%± LW</th>
  <th>Theaters</th><th>Change</th><th>Average</th><th>Total Gross</th>
  <th>Weeks</th><th>Distributor</th><th>New This Week</th><th>Estimated</th>
</tr>
<tr>
  <td>1</td><td>1</td>
  <td><a href="/release/rl2299756545/?ref_=bo_we_table_1">Spider-Man: Brand New Day</a></td>
  <td>$70,711,990</td><td>-51%</td><td>4,539</td><td>+52</td><td>$15,578</td>
  <td>$786,543,660</td><td>3</td><td>Sony Pictures Releasing</td>
  <td>false</td><td>false</td>
</tr>
<tr>
  <td>2</td><td>-</td>
  <td><a href="/release/rl999/">Katseye: Wild Hearts</a></td>
  <td>$3,950,000</td><td>-</td><td>-</td><td>-</td><td>-</td>
  <td>$3,950,000</td><td>1</td><td>Trafalgar</td>
  <td>true</td><td>true</td>
</tr>
</table>
"""


class TestMojoParser(unittest.TestCase):
    def setUp(self):
        self.rows = boxofficemojo.parse_weekend_table(MOJO_HTML)

    def test_row_count(self):
        self.assertEqual(len(self.rows), 2)

    def test_money_and_release_id(self):
        first = self.rows[0]
        self.assertEqual(boxofficemojo.parse_money(first["gross"]), 70711990.0)
        self.assertEqual(first["_release_id"], "rl2299756545")

    def test_columns_are_read_by_name_not_position(self):
        # Les zones internationales n'ont pas de colonne Theaters remplie :
        # le parser doit tolerer un tiret partout.
        second = self.rows[1]
        self.assertIsNone(boxofficemojo.parse_int(second["theaters"]))
        self.assertEqual(second["estimated"], "true")

    def test_missing_money_is_none(self):
        self.assertIsNone(boxofficemojo.parse_money("-"))
        self.assertIsNone(boxofficemojo.parse_money(""))


ALLOCINE_HTML = """
<h1>Box Office Cinéma - Semaine du mercredi 12 août 2026</h1>
<table class="box-office-table responsive-table">
  <tr><th></th><th>Entrées</th><th>Cumul</th><th>Semaine</th></tr>
  <tr class="responsive-table-row">
    <td>
      <div class="label label-ranking">1</div>
      <h2 class="meta-title">
        <a class="meta-title-link" href="/film/fichefilm_gen_cfilm=276608.html">Spider-Man: Brand New Day</a>
      </h2>
      <div class="meta-body">Sony Pictures Releasing France</div>
    </td>
    <td data-heading="Entrées">1&nbsp;189&nbsp;232</td>
    <td data-heading="Cumul">6&nbsp;252&nbsp;860</td>
    <td data-heading="Semaine">1</td>
  </tr>
  <tr class="responsive-table-row">
    <td>
      <div class="label label-ranking">2</div>
      <h2 class="meta-title">
        <a class="meta-title-link" href="/film/fichefilm_gen_cfilm=1000013045.html">L'Odyssée</a>
      </h2>
      <div class="meta-body">Universal Pictures</div>
    </td>
    <td data-heading="Entrées">731&nbsp;224</td>
    <td data-heading="Cumul">6&nbsp;393&nbsp;675</td>
    <td data-heading="Semaine">2</td>
  </tr>
</table>
"""


class TestAllocineParser(unittest.TestCase):
    def setUp(self):
        self.rows = allocine.parse_box_office_table(ALLOCINE_HTML)

    def test_non_breaking_spaces_are_stripped(self):
        self.assertEqual(self.rows[0]["admissions"], 1189232)
        self.assertEqual(self.rows[0]["cumulative"], 6252860)

    def test_metadata(self):
        first = self.rows[0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["film_id"], "276608")
        self.assertEqual(first["distributor"], "Sony Pictures Releasing France")
        self.assertEqual(first["week_in_release"], 1)

    def test_accented_title_kept(self):
        self.assertEqual(self.rows[1]["title"], "L'Odyssée")

    def test_week_label_read_from_page(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(ALLOCINE_HTML, "lxml")
        self.assertEqual(allocine.parse_week_label(soup), date(2026, 8, 12))

    def test_week_label_absent(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<h1>Box Office</h1>", "lxml")
        self.assertIsNone(allocine.parse_week_label(soup))


class TestSteamSpyOwners(unittest.TestCase):
    def test_range_midpoint(self):
        value, raw = steamspy.parse_owners("1,000,000 .. 2,000,000")
        self.assertEqual(value, 1_500_000)
        self.assertEqual(raw, "1,000,000 .. 2,000,000")

    def test_single_value(self):
        value, _ = steamspy.parse_owners("20,000")
        self.assertEqual(value, 20_000)

    def test_garbage(self):
        self.assertEqual(steamspy.parse_owners(""), (None, None))


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Famitsu Sales: Switch 2 tops hardware chart</title>
    <link>https://example.com/famitsu</link>
    <pubDate>Wed, 19 Aug 2026 07:00:00 +0000</pubDate>
    <description>&lt;p&gt;Weekly Japanese chart.&lt;/p&gt;</description>
  </item>
  <item>
    <title>New trailer released</title>
    <link>https://example.com/trailer</link>
    <pubDate>Wed, 19 Aug 2026 08:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""


class TestFeedParser(unittest.TestCase):
    def setUp(self):
        self.items = editorial.parse_feed(RSS)

    def test_items_parsed(self):
        self.assertEqual(len(self.items), 2)
        self.assertEqual(self.items[0]["published_at"], "2026-08-19")

    def test_html_stripped_from_summary(self):
        self.assertEqual(self.items[0]["summary"], "Weekly Japanese chart.")

    def test_keyword_filter(self):
        keywords = ["famitsu", "chart"]
        kept = [i for i in self.items if editorial.matches(i, keywords)]
        self.assertEqual(len(kept), 1)
        self.assertIn("Famitsu", kept[0]["title"])

    def test_no_keywords_keeps_everything(self):
        kept = [i for i in self.items if editorial.matches(i, [])]
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
