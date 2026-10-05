"""Orchestration de la collecte quotidienne.

Regle de conception : une source qui tombe ne doit jamais faire echouer la
collecte. Chaque collecteur est isole, son resultat et son eventuelle erreur
sont journalises dans `collector_runs`, et le rapport hebdomadaire affiche ce
journal. On sait ainsi toujours si une section vide signifie « rien a
signaler » ou « la source ne repond plus ».
"""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import date

from . import db, derive
from .collectors import registry_instances
from .config import Config
from .http import Client

log = logging.getLogger(__name__)


@dataclass
class CollectorOutcome:
    name: str
    label: str
    status: str            # 'ok' | 'vide' | 'desactive' | 'erreur'
    rows: int = 0
    news: int = 0
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""
    # Requetes reseau tentees : -1 si inconnu, 0 pour une source locale (import
    # CSV), qui ne compte donc pas pour juger si « tout a echoue ».
    requests: int = -1


@dataclass
class CollectRun:
    run_id: int
    outcomes: list[CollectorOutcome] = field(default_factory=list)
    derived_rows: int = 0
    titles_resolved: int = 0
    derive_error: str = ""       # echec de l'etape d'agregation, le cas echeant

    @property
    def total_rows(self) -> int:
        return sum(o.rows for o in self.outcomes) + self.derived_rows

    @property
    def total_news(self) -> int:
        return sum(o.news for o in self.outcomes)

    @property
    def failed(self) -> list[CollectorOutcome]:
        return [o for o in self.outcomes if o.status == "erreur"]

    @property
    def all_failed(self) -> bool:
        """True si aucune source active n'a repondu : poste hors ligne, par exemple.

        Une collecte dans ce cas n'a rien apporte : les chiffres de la base sont
        exactement ceux de la veille, voire de bien avant. Les sources locales
        (import CSV), qui n'utilisent pas le reseau, ne comptent pas.
        """
        attempted = [
            o for o in self.outcomes if o.status != "desactive" and o.requests != 0
        ]
        return bool(attempted) and all(o.status == "erreur" for o in attempted)


def is_enabled(config: Config, collector) -> bool:
    """Un collecteur est actif si sa section et sa cle de source le sont."""
    section = "box_office" if collector.domain == "box_office" else "games"
    if not config.raw.get(section, {}).get("enabled", True):
        return False

    # Les sources 'toutes plateformes' vivent dans un sous-bloc.
    all_platforms = config.games.get("all_platforms", {}) or {}
    if collector.name in all_platforms:
        return bool(all_platforms[collector.name])

    sources = config.raw.get(section, {}).get("sources", {}) or {}
    if collector.name in sources:
        return bool(sources[collector.name])
    return True


def collect(config: Config, today: date | None = None, only: list[str] | None = None) -> CollectRun:
    """Lance tous les collecteurs actifs et ecrit en base."""
    today = today or date.today()
    client = Client(delay=config.http_delay)

    with db.session() as conn:
        run_id = db.start_run(conn, "collect")
        run = CollectRun(run_id=run_id)

        for collector in registry_instances(client, config):
            if only and collector.name not in only:
                continue

            outcome = CollectorOutcome(
                name=collector.name, label=collector.label, status="ok"
            )

            if not only and not is_enabled(config, collector):
                outcome.status = "desactive"
                run.outcomes.append(outcome)
                db.log_collector(
                    conn, run_id, collector.name, collector.label, "desactive", 0, 0
                )
                continue

            started = time.monotonic()
            ok_before, failures_before = client.ok, len(client.failures)
            try:
                result = collector.run(today)
                outcome.rows = db.save_observations(conn, result.observations)
                outcome.news = db.save_news(conn, result.news)
                outcome.notes = result.notes
                if outcome.rows == 0 and outcome.news == 0:
                    outcome.status = "vide"
                conn.commit()

                # Une source qui n'a obtenu aucune reponse n'est pas « vide » :
                # elle est en panne (reseau coupe, adresse changee...).
                answered = client.ok - ok_before
                failures = client.failures[failures_before:]
                outcome.requests = answered + len(failures)
                if failures and answered == 0 and outcome.status == "vide":
                    outcome.status = "erreur"
                    outcome.error = failures[0]
                    if len(failures) > 1:
                        outcome.error += f" (+{len(failures) - 1} autre(s) requete(s) en echec)"
            except Exception as exc:  # une source ne doit jamais tout casser
                outcome.status = "erreur"
                outcome.error = f"{type(exc).__name__}: {exc}"
                log.warning("collecteur %s en erreur : %s", collector.name, exc)
                log.debug("%s", traceback.format_exc())

            outcome.duration_ms = int((time.monotonic() - started) * 1000)
            run.outcomes.append(outcome)

            # Trace INFO : c'est elle qu'on retrouve dans logs/ apres une
            # execution planifiee, ou aucune console n'a affiche le tableau.
            log.info(
                "%s : %s, %s ligne(s), %s article(s) en %s ms",
                collector.name,
                outcome.status,
                outcome.rows,
                outcome.news,
                outcome.duration_ms,
            )
            for note in outcome.notes:
                log.info("%s : %s", collector.name, note)

            db.log_collector(
                conn,
                run_id,
                collector.name,
                collector.label,
                outcome.status,
                outcome.rows,
                outcome.duration_ms,
                outcome.error or " | ".join(outcome.notes),
            )

        # --- etapes derivees, apres toutes les sources ---
        try:
            run.titles_resolved = derive.resolve_titles(conn)
            aggregated = derive.worldwide_box_office(
                conn,
                markets=list(config.box_office.get("markets", [])),
                weeks_back=config.backfill_weeks,
            )
            run.derived_rows = db.save_observations(conn, aggregated)
            conn.commit()
            db.log_collector(
                conn,
                run_id,
                "agregat_monde",
                "Box-office monde (somme des zones collectees)",
                "ok" if run.derived_rows else "vide",
                run.derived_rows,
                0,
                f"{run.titles_resolved} titre(s) Steam resolu(s)",
            )
        except Exception as exc:
            log.warning("agregation impossible : %s", exc)
            run.derive_error = f"{type(exc).__name__}: {exc}"
            db.log_collector(
                conn, run_id, "agregat_monde", "Box-office monde", "erreur", 0, 0, str(exc)
            )

        status = "erreur" if run.failed else "ok"
        log.info(
            "collecte terminee : %s observation(s) dont %s agregee(s), "
            "%s article(s), %s source(s) en erreur",
            run.total_rows,
            run.derived_rows,
            run.total_news,
            len(run.failed),
        )
        db.finish_run(
            conn,
            run_id,
            status,
            f"{run.total_rows} observation(s), {run.total_news} article(s)",
        )
    return run
