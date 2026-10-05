"""Stockage SQLite.

Tout est range dans une seule table `observations` au format long : une ligne
= une mesure (un film ou un jeu, un marche, une periode, une metrique). Ce
format evite d'avoir a modifier le schema chaque fois qu'une source apparait.

La cle UNIQUE rend la collecte idempotente : relancer trois fois dans la
journee ne cree pas de doublon, et une donnee revisee par la source ecrase
l'ancienne valeur.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import paths
from .collectors.base import Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    domain       TEXT    NOT NULL,
    market       TEXT    NOT NULL,
    period_type  TEXT    NOT NULL,
    period_start TEXT    NOT NULL,
    period_end   TEXT,
    period_label TEXT,
    rank         INTEGER,
    prev_rank    INTEGER,
    title        TEXT    NOT NULL,
    entity_id    TEXT,
    platform     TEXT    NOT NULL DEFAULT '',
    distributor  TEXT,
    metric       TEXT    NOT NULL,
    value        REAL,
    currency     TEXT,
    reliability  TEXT    NOT NULL,
    extra        TEXT,
    UNIQUE (source, domain, market, period_type, period_start, metric, title, platform)
);

CREATE INDEX IF NOT EXISTS idx_obs_lookup
    ON observations (domain, market, metric, period_start DESC);
CREATE INDEX IF NOT EXISTS idx_obs_period
    ON observations (period_start DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,
    details     TEXT
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    collector   TEXT    NOT NULL,
    label       TEXT,
    status      TEXT    NOT NULL,
    rows        INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    message     TEXT,
    started_at  TEXT
);

CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    source       TEXT NOT NULL,
    published_at TEXT,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    summary      TEXT,
    markets      TEXT,
    domain       TEXT NOT NULL DEFAULT 'games_all'
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at DESC);

-- Memoire de l'interface : quel rapport a deja ete montre, quand la derniere
-- ouverture automatique a eu lieu. Sans cela, l'ouverture hebdomadaire du
-- rapport dans le navigateur se declencherait a chaque lancement.
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

UPSERT = """
INSERT INTO observations (
    collected_at, source, domain, market, period_type, period_start, period_end,
    period_label, rank, prev_rank, title, entity_id, platform, distributor,
    metric, value, currency, reliability, extra
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT (source, domain, market, period_type, period_start, metric, title, platform)
DO UPDATE SET
    collected_at = excluded.collected_at,
    period_end   = excluded.period_end,
    period_label = excluded.period_label,
    rank         = excluded.rank,
    prev_rank    = excluded.prev_rank,
    entity_id    = excluded.entity_id,
    distributor  = excluded.distributor,
    value        = excluded.value,
    currency     = excluded.currency,
    reliability  = excluded.reliability,
    extra        = excluded.extra
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    paths.ensure_dirs()
    conn = sqlite3.connect(path or paths.db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path: Path | None = None) -> Path:
    target = path or paths.db_path()
    conn = connect(target)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return target


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Ecriture
# --------------------------------------------------------------------------- #
def save_observations(conn: sqlite3.Connection, observations: Iterable[Observation]) -> int:
    """Insere ou met a jour un lot d'observations. Renvoie le nombre traite."""
    stamp = utcnow()
    rows = []
    for obs in observations:
        rows.append(
            (
                stamp,
                obs.source,
                obs.domain,
                obs.market,
                obs.period_type,
                obs.period_start.isoformat(),
                obs.period_end.isoformat() if obs.period_end else None,
                obs.period_label,
                obs.rank,
                obs.prev_rank,
                obs.title,
                obs.entity_id,
                obs.platform or "",
                obs.distributor,
                obs.metric,
                obs.value,
                obs.currency,
                obs.reliability,
                json.dumps(obs.extra, ensure_ascii=False) if obs.extra else None,
            )
        )
    if not rows:
        return 0
    conn.executemany(UPSERT, rows)
    return len(rows)


def save_news(conn: sqlite3.Connection, items: Iterable[dict]) -> int:
    stamp = utcnow()
    rows = [
        (
            stamp,
            it["source"],
            it.get("published_at"),
            it["title"],
            it["url"],
            it.get("summary"),
            ",".join(it.get("markets") or []),
            it.get("domain", "games_all"),
        )
        for it in items
    ]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO news (collected_at, source, published_at, title, url,"
        " summary, markets, domain) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT (url) DO NOTHING",
        rows,
    )
    return len(rows)


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, utcnow())
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, details: str = "") -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, details = ? WHERE id = ?",
        (utcnow(), status, details, run_id),
    )
    conn.commit()


def log_collector(
    conn: sqlite3.Connection,
    run_id: int,
    collector: str,
    label: str,
    status: str,
    rows: int,
    duration_ms: int,
    message: str = "",
) -> None:
    conn.execute(
        "INSERT INTO collector_runs"
        " (run_id, collector, label, status, rows, duration_ms, message, started_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (run_id, collector, label, status, rows, duration_ms, message[:2000], utcnow()),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def query(conn: sqlite3.Connection, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def available_periods(
    conn: sqlite3.Connection, domain: str, market: str, metric: str, limit: int = 12
) -> list[str]:
    """Periodes disponibles pour un triplet donne, de la plus recente a la plus ancienne."""
    rows = conn.execute(
        "SELECT period_start, COUNT(*) AS n FROM observations"
        " WHERE domain = ? AND market = ? AND metric = ?"
        " GROUP BY period_start ORDER BY period_start DESC LIMIT ?",
        (domain, market, metric, limit),
    )
    return [r["period_start"] for r in rows]


def chart(
    conn: sqlite3.Connection,
    domain: str,
    market: str,
    metric: str,
    period_start: str,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Un classement : les lignes d'une periode, ordonnees par rang."""
    sql = (
        "SELECT * FROM observations"
        " WHERE domain = ? AND market = ? AND metric = ? AND period_start = ?"
        " ORDER BY (rank IS NULL), rank, value DESC"
    )
    params: list = [domain, market, metric, period_start]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params))


def recent_news(conn: sqlite3.Connection, since: str, limit: int = 40) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM news WHERE COALESCE(published_at, collected_at) >= ?"
            " ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
            (since, limit),
        )
    )


def last_run_log(conn: sqlite3.Connection, kind: str = "collect") -> list[sqlite3.Row]:
    row = conn.execute(
        "SELECT id FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
    ).fetchone()
    if not row:
        return []
    return list(
        conn.execute(
            "SELECT * FROM collector_runs WHERE run_id = ? ORDER BY id", (row["id"],)
        )
    )


def _successful_collects(conn: sqlite3.Connection, limit: int = 20) -> list[datetime]:
    """Debut (heure locale) des dernieres collectes abouties, de la plus recente.

    « Abouti » : terminee, avec au moins une source qui a repondu. Un poste
    hors ligne fait echouer toutes les sources : ce n'est pas une collecte.
    """
    rows = conn.execute(
        "SELECT r.started_at FROM runs r"
        " WHERE r.kind = 'collect' AND r.finished_at IS NOT NULL"
        " AND EXISTS (SELECT 1 FROM collector_runs c"
        "             WHERE c.run_id = r.id AND c.status IN ('ok', 'vide'))"
        " ORDER BY r.id DESC LIMIT ?",
        (limit,),
    )
    found: list[datetime] = []
    for row in rows:
        try:
            found.append(datetime.fromisoformat(row["started_at"]).astimezone())
        except (TypeError, ValueError):
            continue
    return found


def collected_on(conn: sqlite3.Connection, day: date) -> bool:
    """True si une collecte a deja abouti ce jour-la (heure locale).

    Le declenchement suivant doit reessayer apres un echec total, d'ou la
    definition de « abouti » ci-dessus.
    """
    return any(started.date() == day for started in _successful_collects(conn))


def last_successful_collect(conn: sqlite3.Connection) -> datetime | None:
    """Date de la derniere collecte abouti, ou None s'il n'y en a jamais eu."""
    found = _successful_collects(conn, limit=1)
    return found[0] if found else None


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    news = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    per_domain = {
        r["domain"]: r["n"]
        for r in conn.execute(
            "SELECT domain, COUNT(*) AS n FROM observations"
            " GROUP BY domain ORDER BY n DESC"
        )
    }
    span = conn.execute(
        "SELECT MIN(period_start), MAX(period_start) FROM observations"
    ).fetchone()
    return {
        "observations": total,
        "news": news,
        "per_domain": per_domain,
        "first_period": span[0],
        "last_period": span[1],
    }
