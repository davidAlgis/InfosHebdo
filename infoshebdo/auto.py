"""Verification automatique : collecte du jour, puis rapport de la semaine.

Appelee par l'application residente (au demarrage, puis toutes les 30
minutes) et par `infoshebdo auto` (cron sous Linux). Elle peut donc tourner
plusieurs fois par jour : chaque etape verifie d'abord si elle a deja ete faite.

* collecte : une fois par jour. Elle alimente la base meme les jours ou le
  rapport ne s'ouvre pas, parce que les joueurs simultanes Steam sont un
  instantane et que le box-office international est revise apres coup.
* rapport : une fois par semaine ISO, le premier jour ou la tache s'execute.
  Poste allume le lundi, il s'ouvre le lundi ; sinon le mardi, etc.

La collecte passe avant le rapport, pour que le rapport ouvert contienne les
chiffres du jour.

Quand la collecte echoue, le rapport s'ouvre quand meme, mais avec un
avertissement en tete de page, et la fenetre d'erreur (voir `cli.cmd_auto`)
detaille ce qui a echoue. Apres un echec total, la semaine n'est pas marquee
comme vue : le rapport a jour s'ouvrira des qu'une collecte aboutira.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from . import db, pipeline, report as report_module, viewer
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Problem:
    """Une chose qui a mal tourne, avec le message exact a montrer."""

    source: str
    message: str


@dataclass
class AutoResult:
    collected: bool = False       # une collecte a ete lancee pendant cet appel
    collect_failures: int = 0     # sources en erreur lors de cette collecte
    all_failed: bool = False      # aucune source n'a repondu
    opened: bool = False          # le rapport a ete ouvert pendant cet appel
    report_path: Path | None = None
    reason: str = ""              # pourquoi le rapport n'a pas ete ouvert
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Code retour 0 : rien n'a mal tourne."""
        return not self.problems

    @property
    def explanation(self) -> str:
        """Ce que ces problemes changent pour le rapport, en une phrase."""
        if self.all_failed:
            if self.opened:
                return (
                    "Aucune source n'a repondu. Le rapport s'est ouvert avec les "
                    "dernieres donnees connues, signalees par un avertissement en "
                    "haut de page ; il sera rouvert des qu'une collecte aboutira."
                )
            return (
                "Aucune source n'a repondu : les chiffres de la base datent de la "
                "derniere collecte reussie."
            )
        if self.opened:
            return (
                "Le rapport a ete ouvert, mais les sections concernees sont "
                "incompletes : la source en erreur y est indiquee."
            )
        return (
            "Le rapport de la semaine n'a pas pu etre ouvert automatiquement. "
            "Il est enregistre dans le dossier reports/."
        )


def problems_from(run_: "pipeline.CollectRun") -> list[Problem]:
    """Les sources en erreur d'une collecte, plus l'echec d'agregation eventuel."""
    problems = [Problem(source=o.label or o.name, message=o.error) for o in run_.failed]
    if run_.derive_error:
        problems.append(Problem(source="Agregat box-office monde", message=run_.derive_error))
    return problems


def _notices(result: AutoResult, failed_labels: list[str]) -> list[str]:
    """Avertissements a afficher en tete du rapport."""
    if result.all_failed:
        with db.session() as conn:
            last = db.last_successful_collect(conn)
        since = (
            f"la derniere collecte reussie ({last:%d/%m/%Y})"
            if last
            else "une collecte anterieure"
        )
        return [
            "La collecte du jour a echoue sur toutes les sources : les chiffres "
            f"ci-dessous datent de {since} et ne refletent pas necessairement "
            "la semaine ecoulee."
        ]
    if failed_labels:
        return [
            "Certaines sources n'ont pas pu etre collectees aujourd'hui "
            f"({', '.join(failed_labels)}) : les sections correspondantes "
            "peuvent etre incompletes."
        ]
    return []


def run(
    config: Config,
    today: date | None = None,
    force: bool = False,
    collect_fn: Callable[..., "pipeline.CollectRun"] | None = None,
    open_fn: Callable[[Path], bool] | None = None,
) -> AutoResult:
    """Collecte si besoin, ouvre le rapport de la semaine s'il n'a pas ete vu.

    `force` ouvre le rapport meme s'il a deja ete montre cette semaine.
    `collect_fn` et `open_fn` sont injectables pour les tests : ni reseau ni
    navigateur.
    """
    today = today or date.today()
    collect_fn = collect_fn or pipeline.collect
    open_fn = open_fn or viewer.open_report
    result = AutoResult()
    failed_labels: list[str] = []

    with db.session() as conn:
        needs_collect = not db.collected_on(conn, today)

    if needs_collect:
        run_ = collect_fn(config, today=today)
        result.collected = True
        result.collect_failures = len(run_.failed)
        result.all_failed = run_.all_failed
        failed_labels = [o.label or o.name for o in run_.failed]
        result.problems += problems_from(run_)
    else:
        log.info("collecte deja faite aujourd'hui, rien a relancer")

    if not config.report.get("auto_open", True):
        result.reason = "ouverture automatique desactivee"
        log.info(result.reason)
        return result

    with db.session() as conn:
        if viewer.already_shown(conn, today) and not force:
            result.reason = "rapport de la semaine deja ouvert"
            log.info(result.reason)
            return result
        # Apres un echec total, le rapport montre de vieux chiffres : on ne le
        # rouvre pas a chaque tentative de la journee (ouverture de session...).
        if result.all_failed and not force and viewer.stale_shown_on(conn, today):
            result.reason = "rapport deja ouvert aujourd'hui malgre l'echec de la collecte"
            log.info(result.reason)
            return result

    notices = _notices(result, failed_labels)
    report, html, text = report_module.build(config, today, notices=notices)
    html_path, _ = report_module.save(html, text, today)
    result.report_path = html_path
    if not report.has_data:
        log.warning("aucune donnee en base : le rapport ouvert sera vide")

    if not open_fn(html_path):
        # Pas de marque « vu » : le prochain declenchement retentera.
        result.reason = "ouverture impossible"
        result.problems.append(
            Problem(
                source="Navigateur",
                message=f"Le rapport n'a pas pu etre ouvert automatiquement : {html_path}",
            )
        )
        return result

    with db.session() as conn:
        if result.all_failed:
            # Rapport « de secours » : la semaine reste a ouvrir quand les
            # donnees seront a jour.
            viewer.mark_stale_shown(conn, today)
        else:
            viewer.mark_shown(conn, today)
    result.opened = True
    return result
