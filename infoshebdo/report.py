"""Rendu du rapport : HTML (ouvert dans le navigateur) et texte brut
(meme contenu, pour la console ou `report --print-text`).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import paths
from .analysis import Report, build_report

TEMPLATE = "report.html.j2"
LINE_WIDTH = 78


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(paths.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(report: Report, show_empty: bool = True) -> str:
    template = _environment().get_template(TEMPLATE)
    return template.render(report=report, show_empty=show_empty)


def render_text(report: Report) -> str:
    """Version texte : meme information, sans mise en forme."""
    out: list[str] = []
    add = out.append

    add(report.title.upper())
    add(
        f"{report.week_label} - du {report.period_start:%d/%m/%Y} "
        f"au {report.period_end:%d/%m/%Y}"
    )
    add("=" * LINE_WIDTH)
    add("")
    add(report.headline)
    add("")
    for notice in report.notices:
        add(_wrap(f"ATTENTION : {notice}", indent="  "))
        add("")

    for section in report.sections:
        add("")
        add(section.title.upper())
        add("-" * LINE_WIDTH)
        if section.intro:
            add(_wrap(section.intro))
        for table in section.tables:
            add("")
            header = f"  {table.title}"
            if table.period_label:
                header += f"  [{table.period_label}]"
            add(header)
            add(f"  source : {table.source_label} ({table.reliability})")
            if table.warning:
                add(_wrap(f"  ATTENTION : {table.warning}", indent="  "))
            if not table.rows:
                add(f"  (aucune donnee) {table.note}")
                continue
            # Sans en-tetes, la colonne de valeur est ambigue : chez Steam elle
            # porte un nombre de semaines consecutives, pas un volume.
            headers = list(table.columns[:4]) + [""] * (4 - len(table.columns[:4]))
            add(
                f"  {headers[0][:4]:>4} {headers[1][:44]:<44} "
                f"{headers[2][:14]:>14} {headers[3][:8]:>8}"
            )
            for row in table.rows:
                rank = f"{row.rank:>3}." if row.rank else "   -"
                change = row.delta_pct or row.move or ""
                line = f"  {rank} {row.title[:44]:<44} {row.value_fmt:>14} {change:>8}"
                add(line)
            if table.note:
                add(_wrap(f"  Note : {table.note}", indent="  "))

        if section.news:
            add("")
            add("  Publications de classements reperees :")
            for item in section.news:
                add(f"   - {item['title']}")
                add(f"     {item['url']}")

    if report.legend:
        add("")
        add("FIABILITE")
        add("-" * LINE_WIDTH)
        for key, help_text in report.legend.items():
            add(_wrap(f"  {key} : {help_text}", indent="    "))

    if report.run_log:
        add("")
        add("DERNIERE COLLECTE")
        add("-" * LINE_WIDTH)
        for entry in report.run_log:
            add(f"  {entry['status']:<10} {entry['rows']:>5} lignes  {entry['label']}")
            if entry["status"] == "erreur" and entry["message"]:
                add(f"             {entry['message'][:120]}")

    add("")
    add(f"Genere le {report.generated_at} par InfosHebdo.")
    return "\n".join(out)


def _wrap(text: str, indent: str = "") -> str:
    import textwrap

    return "\n".join(
        textwrap.wrap(
            text, width=LINE_WIDTH, initial_indent="", subsequent_indent=indent
        )
    )


def build(
    config, today: date | None = None, notices: list[str] | None = None
) -> tuple[Report, str, str]:
    """Construit le rapport et ses deux rendus.

    `notices` : avertissements affiches en tete (collecte echouee, par exemple).
    """
    report = build_report(config, today, notices)
    show_empty = config.report.get("empty_sections", "show") != "hide"
    return report, render_html(report, show_empty), render_text(report)


def save(html: str, text: str, today: date | None = None) -> tuple[Path, Path]:
    """Ecrit les deux rendus dans reports/ et renvoie leurs chemins."""
    paths.ensure_dirs()
    stamp = (today or date.today()).isoformat()
    html_path = paths.REPORTS_DIR / f"infoshebdo-{stamp}.html"
    text_path = paths.REPORTS_DIR / f"infoshebdo-{stamp}.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    return html_path, text_path
