"""Import manuel de chiffres non automatisables.

Destine aux donnees que seul un humain peut recuperer : classement GSD relaye
sur les reseaux, top 10 Circana, tableau Famitsu, chiffre communique par un
editeur. On depose un CSV dans data/import/ et il est avale a la collecte
suivante, puis deplace dans data/import/traites/.

Format attendu (en-tetes obligatoires, ordre libre) :

    market,period_start,title,metric,value
    FR,2026-08-10,EA SPORTS FC 26,units,45000

Colonnes optionnelles :
    platform, rank, prev_rank, distributor, currency, reliability,
    period_end, period_type, source, note

Valeurs par defaut : period_type=week, reliability=manuel, source=manual.
Les lignes invalides sont signalees dans le journal de collecte plutot que
d'interrompre l'import.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import paths
from .base import (
    GAMES_ALL,
    MANUAL,
    UNITS,
    Collector,
    CollectorResult,
    Observation,
    register,
)

log = logging.getLogger(__name__)

REQUIRED = {"market", "period_start", "title", "metric", "value"}


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: str) -> float | None:
    text = (value or "").strip().replace(" ", "").replace(" ", "")
    text = text.replace(",", ".") if text.count(",") == 1 and "." not in text else text
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    number = _parse_float(value)
    return int(number) if number is not None else None


def read_csv_rows(path: Path) -> tuple[list[Observation], list[str]]:
    """Convertit un CSV en observations. Renvoie (observations, erreurs)."""
    observations: list[Observation] = []
    errors: list[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)

        headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
        missing = REQUIRED - headers
        if missing:
            return [], [f"{path.name} : colonnes manquantes {sorted(missing)}"]

        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            start = _parse_date(row.get("period_start", ""))
            value = _parse_float(row.get("value", ""))
            title = row.get("title", "")
            market = row.get("market", "").upper()

            if not (start and title and market) or value is None:
                errors.append(f"{path.name} ligne {line_no} : ligne incomplete, ignoree.")
                continue

            end = _parse_date(row.get("period_end", "")) or start + timedelta(days=6)
            observations.append(
                Observation(
                    source=row.get("source") or "manual",
                    domain=GAMES_ALL,
                    market=market,
                    period_type=row.get("period_type") or "week",
                    period_start=start,
                    period_end=end,
                    # La note est une precision de provenance, pas un
                    # libelle de periode : la periode reste la semaine, que le
                    # rapport formate lui-meme.
                    period_label=None,
                    title=title,
                    platform=row.get("platform", ""),
                    distributor=row.get("distributor") or None,
                    metric=row.get("metric") or UNITS,
                    value=value,
                    currency=row.get("currency") or None,
                    rank=_parse_int(row.get("rank", "")),
                    prev_rank=_parse_int(row.get("prev_rank", "")),
                    reliability=row.get("reliability") or MANUAL,
                    extra={
                        "imported_from": path.name,
                        "note": row.get("note") or None,
                    },
                )
            )
    return observations, errors


@register
class ManualImportCollector(Collector):
    name = "manual_import"
    label = "Import manuel (CSV) - ventes toutes plateformes"
    domain = GAMES_ALL
    source_url = "data/import/*.csv"
    provides = "Chiffres saisis a la main : GSD, Circana, Famitsu, communiques editeurs"
    reliability = MANUAL

    def run(self, today: date) -> CollectorResult:
        result = CollectorResult()
        block = self.config.games.get("all_platforms", {}) or {}
        if not block.get("manual_import", False):
            result.notes.append("Import manuel desactive.")
            return result

        paths.ensure_dirs()
        files = sorted(p for p in paths.IMPORT_DIR.glob("*.csv") if p.is_file())
        if not files:
            result.notes.append(
                f"Aucun CSV dans {paths.IMPORT_DIR} (deposez-y les chiffres a integrer)."
            )
            return result

        processed = paths.PROCESSED_IMPORT_DIR
        processed.mkdir(parents=True, exist_ok=True)

        for path in files:
            try:
                observations, errors = read_csv_rows(path)
            except OSError as exc:
                result.notes.append(f"{path.name} : {exc}")
                continue

            result.observations.extend(observations)
            result.notes.extend(errors)
            result.notes.append(f"{path.name} : {len(observations)} ligne(s) importee(s).")

            if observations:
                target = processed / path.name
                if target.exists():
                    stem, suffix = path.stem, path.suffix
                    target = processed / f"{stem}-{date.today().isoformat()}{suffix}"
                try:
                    path.replace(target)
                except OSError as exc:
                    result.notes.append(f"{path.name} : deplacement impossible ({exc}).")
        return result
