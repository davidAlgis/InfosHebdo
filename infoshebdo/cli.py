"""Interface en ligne de commande.

L'application (`infoshebdo ui`, ou l'executable sans argument) fait tout
toute seule : elle collecte chaque jour et ouvre le rapport chaque semaine. Ces
commandes servent a la mise au point, au diagnostic, et a cron sous Linux :

    infoshebdo auto             # collecte du jour, puis ouvre le rapport de la
                                # semaine s'il n'a pas ete vu
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

from . import config as config_module
from . import auto as auto_module
from . import db, paths, report as report_module, viewer
from .collectors import registry_instances
from .collectors.base import market_label
from .http import Client
from .pipeline import collect, is_enabled

STATUS_MARK = {"ok": "[ok]", "vide": "[--]", "desactive": "[  ]", "erreur": "[KO]"}

log = logging.getLogger(__name__)


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def _setup_logging(verbose: bool) -> None:
    """Journal sur la console et dans logs/.

    Le fichier est indispensable pour les executions planifiees : la tache
    Windows tourne sans console, et c'est le seul endroit ou retrouver ce qui
    s'est passe a neuf heures du matin.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # La console reste sobre : les commandes impriment deja un tableau lisible,
    # inutile de le doubler d'un flux de journal. Seuls les avertissements
    # passent, sauf en mode detaille.
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    # Le fichier, lui, garde tout : une tache planifiee tourne sans console et
    # c'est le seul endroit ou retrouver ce qui s'est passe a neuf heures.
    try:
        paths.ensure_dirs()
        handler = RotatingFileHandler(
            paths.LOG_DIR / "infoshebdo.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(handler)
    except OSError as exc:
        # Un journal inaccessible ne doit pas empecher la collecte.
        print(f"Journal fichier indisponible : {exc}", file=sys.stderr)

    logging.getLogger("urllib3").setLevel(
        logging.DEBUG if verbose else logging.WARNING
    )


def _parse_day(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def cmd_init_db(args, cfg) -> int:
    target = db.init()
    print(f"Base prete : {target}")
    print(f"Dossier d'import manuel : {paths.IMPORT_DIR}")
    return 0


def cmd_collect(args, cfg) -> int:
    if args.backfill:
        cfg.raw["backfill_weeks"] = args.backfill
    run = collect(cfg, today=_parse_day(args.date), only=args.only)

    print(f"\nCollecte #{run.run_id} - {_parse_day(args.date)}")
    print("-" * 72)
    for outcome in run.outcomes:
        mark = STATUS_MARK.get(outcome.status, "[??]")
        print(f"{mark} {outcome.label:<52} {outcome.rows:>5} lignes {outcome.duration_ms:>6} ms")
        if outcome.error:
            print(f"     erreur : {outcome.error}")
        for note in outcome.notes[:4]:
            print(f"     - {note}")
    print("-" * 72)
    print(
        f"{run.total_rows} observation(s), {run.total_news} article(s), "
        f"{run.derived_rows} ligne(s) agregee(s)."
    )
    if run.failed:
        print(f"{len(run.failed)} source(s) en erreur.", file=sys.stderr)
        return 1
    return 0


def cmd_report(args, cfg) -> int:
    if args.top:
        cfg.raw["top_n"] = args.top
    today = _parse_day(args.date)
    report, html, text = report_module.build(cfg, today)

    if not report.has_data:
        log.warning("aucune donnee en base : lancer d'abord `infoshebdo collect`")
        print("Aucune donnee en base : lancez d'abord `infoshebdo collect`.", file=sys.stderr)

    html_path, text_path = report_module.save(html, text, today)
    print(f"Rapport HTML  : {html_path}")
    print(f"Rapport texte : {text_path}")

    log.info("rapport ecrit : %s", html_path)

    if args.print_text:
        print()
        print(text)

    if args.open:
        if not viewer.open_report(html_path):
            print("Ouverture dans le navigateur impossible.", file=sys.stderr)
            return 1
    return 0


def _show_problems(args, problems, explanation: str) -> None:
    """Fenetre d'erreur : un lancement automatique n'a pas de console.

    Un echec d'affichage (pas d'ecran, par exemple sous cron) ne doit pas
    masquer l'erreur initiale : elle est deja dans le journal.
    """
    if args.no_window:
        return
    try:
        from .ui.error_window import show

        show(problems, explanation=explanation, report_path=viewer.latest_report())
    except Exception as exc:  # noqa: BLE001 - dependant du systeme
        log.warning("fenetre d'erreur impossible : %s", exc)
        print(f"Fenetre d'erreur impossible : {exc}", file=sys.stderr)


def cmd_auto(args, cfg) -> int:
    """Verification automatique : collecte du jour, puis rapport de la semaine."""
    today = _parse_day(args.date)
    try:
        result = auto_module.run(cfg, today=today, force=args.force)
    except Exception as exc:  # noqa: BLE001 - la tache doit toujours s'expliquer
        log.exception("echec de la commande auto")
        print(f"Erreur : {type(exc).__name__}: {exc}", file=sys.stderr)
        _show_problems(
            args,
            [auto_module.Problem(
                source="Erreur inattendue",
                message="".join(traceback.format_exception(exc)),
            )],
            "La commande s'est arretee avant d'avoir termine. Le detail "
            "complet est aussi dans logs/infoshebdo.log.",
        )
        return 2

    if result.collected:
        print(
            "Collecte effectuee"
            + (f", {result.collect_failures} source(s) en erreur." if result.collect_failures else ".")
        )
    else:
        print("Collecte deja faite aujourd'hui.")
    if result.opened:
        print(f"Rapport ouvert dans le navigateur : {result.report_path}")
    else:
        print(f"Rapport non ouvert : {result.reason}.")
    for problem in result.problems:
        print(f"Probleme - {problem.source} : {problem.message}", file=sys.stderr)
    if result.problems:
        _show_problems(args, result.problems, result.explanation)
    return 0 if result.ok else 1


def cmd_sources(args, cfg) -> int:
    client = Client(delay=cfg.http_delay)
    print("\nSources declarees")
    print("=" * 78)
    for collector in registry_instances(client, cfg):
        state = "actif" if is_enabled(cfg, collector) else "inactif"
        print(f"\n{collector.label}   [{state}]")
        print(f"  cle          : {collector.name}")
        print(f"  domaine      : {collector.domain}")
        print(f"  fiabilite    : {collector.reliability}")
        print(f"  source       : {collector.source_url}")
        print(f"  fournit      : {collector.provides}")
    print("\n" + "=" * 78)
    print("Marches box-office :", ", ".join(
        market_label(m) for m in cfg.box_office.get("markets", [])
    ))
    print("Marches Steam      :", ", ".join(
        market_label(m) for m in cfg.games.get("steam_markets", [])
    ))
    print(f"Rattrapage         : {cfg.backfill_weeks} semaines a chaque collecte")
    return 0


def cmd_status(args, cfg) -> int:
    with db.session() as conn:
        info = db.stats(conn)
        print(f"\nBase : {paths.db_path()}")
        print(f"  observations : {info['observations']}")
        print(f"  articles     : {info['news']}")
        if info["first_period"]:
            print(f"  periode      : {info['first_period']} -> {info['last_period']}")
        for domain, count in info["per_domain"].items():
            print(f"  {domain:<12} : {count}")

        print("\nDerniere collecte")
        log_rows = db.last_run_log(conn, "collect")
        if not log_rows:
            print("  aucune collecte enregistree.")
        for row in log_rows:
            mark = STATUS_MARK.get(row["status"], "[??]")
            print(f"  {mark} {row['label'] or row['collector']:<50} {row['rows']:>5} lignes")
            if row["status"] == "erreur" and row["message"]:
                print(f"       {row['message'][:120]}")

        print("\nRapport")
        shown = db.get_state(conn, viewer.STATE_KEY)
        current = viewer.week_key(date.today())
        latest = viewer.latest_report()
        print(f"  dernier fichier       : {latest.name if latest else 'aucun'}")
        print(f"  derniere semaine vue  : {shown or 'jamais'}")
        if cfg.report.get("auto_open", True):
            print(
                "  semaine en cours      : "
                + (f"{current}, deja ouvert" if shown == current else f"{current}, a ouvrir")
            )
        else:
            print("  ouverture automatique : desactivee (report.auto_open)")
    return 0


def cmd_ui(args, cfg) -> int:
    """Ouvre l'interface graphique."""
    from .ui import launch

    return launch(minimized=args.minimized)


def cmd_selftest(args, cfg) -> int:
    """Verifie qu'une installation est complete : modules, gabarit, icone.

    Sert surtout apres la fabrication de l'executable : un module oublie par
    PyInstaller ne se voit qu'au moment de s'en servir. Ne cree rien (hors
    `--output`) et n'ouvre aucune fenetre.

    L'executable n'a pas de console : `--output FICHIER` recopie le rapport dans
    un fichier, que le script de fabrication relit.
    """
    import importlib
    from datetime import date as _date

    from . import assets

    failures = 0
    lines: list[str] = []

    def emit(line: str) -> None:
        lines.append(line)
        print(line)

    def check(name: str, probe) -> None:
        nonlocal failures
        try:
            detail = probe() or ""
            emit(f"[ok] {name}" + (f" : {detail}" if detail else ""))
        except Exception as exc:  # noqa: BLE001 - on veut tout rapporter
            failures += 1
            emit(f"[KO] {name} : {type(exc).__name__}: {exc}")

    for module in ("requests", "bs4", "lxml.etree", "yaml", "jinja2", "dotenv",
                   "tkinter", "PIL", "pystray"):
        check(f"module {module}", lambda m=module: importlib.import_module(m) and "")

    for module in ("infoshebdo.resident", "infoshebdo.instance", "infoshebdo.ui.app",
                   "infoshebdo.ui.error_window", "infoshebdo.ui.tray"):
        check(f"module {module}", lambda m=module: importlib.import_module(m) and "")

    def template() -> str:
        path = paths.TEMPLATES_DIR / report_module.TEMPLATE
        if not path.exists():
            raise FileNotFoundError(path)
        html = report_module.render_html(
            report_module.Report(
                title="Essai", generated_at="-", week_label="-",
                period_start=_date.today(), period_end=_date.today(),
            )
        )
        if "<!doctype html>" not in html.lower():
            raise ValueError("rendu inattendu")
        return str(path)

    def icon() -> str:
        path = assets.icon_path()
        if path is None or not path.exists():
            raise FileNotFoundError("icone introuvable")
        return str(path)

    check("gabarit du rapport", template)
    check("icone", icon)
    check("dossier de donnees", lambda: str(paths.PROJECT_DIR))

    emit("")
    emit("Installation complete." if not failures else f"{failures} verification(s) en echec.")
    if args.output:
        Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failures else 0


def cmd_config(args, cfg) -> int:
    """Affiche la configuration effective, ou la reecrit proprement."""
    if args.write:
        path = config_module.save(cfg.raw)
        print(f"Configuration reecrite : {path}")
        return 0
    print(f"# fichier : {paths.CONFIG_FILE}")
    print(config_module.render_yaml(cfg.raw))
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infoshebdo",
        description="Veille hebdomadaire box-office et ventes de jeux video.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="journal detaille")
    parser.add_argument("--config", help="chemin d'un config.yaml alternatif")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="cree la base et les dossiers").set_defaults(
        func=cmd_init_db
    )

    collect_parser = subparsers.add_parser(
        "collect", help="collecte quotidienne de toutes les sources actives"
    )
    collect_parser.add_argument("--date", help="simuler une autre date (AAAA-MM-JJ)")
    collect_parser.add_argument(
        "--backfill", type=int, help="nombre de semaines a rattraper"
    )
    collect_parser.add_argument(
        "--only", nargs="+", metavar="CLE", help="ne lancer que ces collecteurs"
    )
    collect_parser.set_defaults(func=cmd_collect)

    report_parser = subparsers.add_parser(
        "report", help="genere le rapport hebdomadaire depuis la base"
    )
    report_parser.add_argument("--date", help="date de reference (AAAA-MM-JJ)")
    report_parser.add_argument(
        "--open", action="store_true", help="ouvrir le rapport dans le navigateur"
    )
    report_parser.add_argument("--top", type=int, help="nombre de lignes par classement")
    report_parser.add_argument(
        "--print-text", action="store_true", help="afficher la version texte"
    )
    report_parser.set_defaults(func=cmd_report)

    auto_parser = subparsers.add_parser(
        "auto",
        help="verification automatique : collecte du jour, puis ouvre le rapport de la "
             "semaine s'il n'a pas encore ete vu",
    )
    auto_parser.add_argument("--date", help="date de reference (AAAA-MM-JJ)")
    auto_parser.add_argument(
        "--force", action="store_true",
        help="ouvrir le rapport meme s'il l'a deja ete cette semaine",
    )
    auto_parser.add_argument(
        "--no-window", action="store_true",
        help="ne pas afficher la fenetre d'erreur (le detail reste dans le journal)",
    )
    auto_parser.set_defaults(func=cmd_auto)

    subparsers.add_parser(
        "sources", help="lister les sources, leur etat et ce qu'elles fournissent"
    ).set_defaults(func=cmd_sources)
    subparsers.add_parser(
        "status", help="etat de la base, de la derniere collecte et du rapport"
    ).set_defaults(func=cmd_status)
    ui_parser = subparsers.add_parser(
        "ui", help="ouvrir l'interface graphique (reglages et lancement)"
    )
    ui_parser.add_argument(
        "--minimized", action="store_true",
        help="demarrer replie dans la zone de notification (lancement de session)",
    )
    ui_parser.set_defaults(func=cmd_ui)

    selftest_parser = subparsers.add_parser(
        "selftest", help="verifier qu'une installation est complete (modules, gabarit, icone)"
    )
    selftest_parser.add_argument(
        "--output", metavar="FICHIER", help="recopier le rapport dans ce fichier"
    )
    selftest_parser.set_defaults(func=cmd_selftest)

    config_parser = subparsers.add_parser(
        "config", help="afficher la configuration effective"
    )
    config_parser.add_argument(
        "--write", action="store_true",
        help="reecrire config.yaml a partir de la configuration effective",
    )
    config_parser.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Sans argument (double-clic sur l'executable) : l'application.
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv) or ["ui"])
    _setup_logging(args.verbose)

    cfg = config_module.load(Path(args.config) if args.config else None)
    try:
        return int(args.func(args, cfg) or 0)
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
