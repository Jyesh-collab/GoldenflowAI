"""The event store - single source of truth.

One canonical event shape, one place to read it from. Phase 2 mines from here,
Phase 3 joins against here, Phase 6 attributes escaped defects from here. Nothing
downstream talks to a vendor API at analysis time.

Two things live in this module:

* :func:`generate_ddl` emits warehouse DDL for the real deployment (BigQuery or
  ClickHouse), partitioned and clustered for the sequence queries Phase 2 runs.
* :class:`EventStore` is a runnable SQLite reference implementation with identical
  semantics, so the pipeline can be developed, tested and demonstrated without
  provisioning a warehouse.

The SQLite store is a reference, not a production target. It exists so that every
downstream phase has something real to read on day one.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator

from pydantic import BaseModel, Field


class Dialect(str, Enum):
    BIGQUERY = "bigquery"
    CLICKHOUSE = "clickhouse"
    SQLITE = "sqlite"


class CanonicalEvent(BaseModel):
    """One normalised event. Every vendor shape converges here."""

    event_id: str = ""
    event_name: str
    event_type: str = "interaction"

    screen_id: str | None = Field(
        default=None,
        description="Canonical screen resolved via the Phase 0 taxonomy. None means "
        "the tag did not resolve - an orphan, tracked by the quality monitors.",
    )
    screen_tag: str | None = None

    session_id: str | None = None
    user_id: str | None = None
    anonymous_id: str | None = None

    timestamp: datetime
    received_at: datetime | None = None

    app_version: str | None = None
    platform: str | None = None
    source: str = "unknown"

    properties: dict[str, Any] = Field(default_factory=dict)
    taxonomy_fingerprint: str | None = None

    def compute_id(self) -> str:
        """Deterministic identity, so re-ingesting the same batch is idempotent.

        Derived from content rather than assigned on arrival: at-least-once delivery
        is the norm across every vendor in the stack, and a random ID would turn
        every redelivery into a duplicate row that inflates journey frequencies.
        """
        payload = "|".join([
            self.session_id or "",
            self.event_name,
            self.screen_tag or "",
            self.timestamp.astimezone(timezone.utc).isoformat(),
            json.dumps(self.properties, sort_keys=True, default=str),
        ])
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def finalised(self) -> "CanonicalEvent":
        data = self.model_copy()
        if not data.event_id:
            data.event_id = data.compute_id()
        if data.received_at is None:
            data.received_at = datetime.now(timezone.utc)
        return data


# --------------------------------------------------------------------- schema

COLUMNS: list[tuple[str, str, str, str]] = [
    # (name, bigquery, clickhouse, sqlite)
    ("event_id", "STRING NOT NULL", "String", "TEXT PRIMARY KEY"),
    ("event_name", "STRING NOT NULL", "LowCardinality(String)", "TEXT NOT NULL"),
    ("event_type", "STRING", "LowCardinality(String)", "TEXT"),
    ("screen_id", "STRING", "LowCardinality(Nullable(String))", "TEXT"),
    ("screen_tag", "STRING", "Nullable(String)", "TEXT"),
    ("session_id", "STRING", "Nullable(String)", "TEXT"),
    ("user_id", "STRING", "Nullable(String)", "TEXT"),
    ("anonymous_id", "STRING", "Nullable(String)", "TEXT"),
    ("timestamp", "TIMESTAMP NOT NULL", "DateTime64(3)", "TEXT NOT NULL"),
    ("received_at", "TIMESTAMP", "DateTime64(3)", "TEXT"),
    ("app_version", "STRING", "LowCardinality(Nullable(String))", "TEXT"),
    ("platform", "STRING", "LowCardinality(Nullable(String))", "TEXT"),
    ("source", "STRING", "LowCardinality(String)", "TEXT"),
    ("properties", "JSON", "String", "TEXT"),
    ("taxonomy_fingerprint", "STRING", "Nullable(String)", "TEXT"),
]

RISK_COLUMNS: list[tuple[str, str, str, str]] = [
    ("signal_id", "STRING NOT NULL", "String", "TEXT PRIMARY KEY"),
    ("kind", "STRING NOT NULL", "LowCardinality(String)", "TEXT NOT NULL"),
    ("session_id", "STRING", "Nullable(String)", "TEXT"),
    ("user_id", "STRING", "Nullable(String)", "TEXT"),
    ("timestamp", "TIMESTAMP NOT NULL", "DateTime64(3)", "TEXT NOT NULL"),
    ("fatal", "BOOL", "UInt8", "INTEGER"),
    ("title", "STRING", "String", "TEXT"),
    ("screen_id", "STRING", "Nullable(String)", "TEXT"),
    ("position_in_session", "INT64", "Nullable(Int32)", "INTEGER"),
    ("source", "STRING", "LowCardinality(String)", "TEXT"),
    ("properties", "JSON", "String", "TEXT"),
]


def generate_ddl(dialect: Dialect | str, *, dataset: str = "goldenflow") -> str:
    """Emit CREATE TABLE DDL for the target warehouse.

    Partitioning and ordering are chosen for the access pattern Phase 2 actually
    has: read every event for a session, ordered by time, over a bounded date
    range. Getting this wrong makes sessionisation a full-table scan.
    """
    dialect = Dialect(dialect)

    if dialect is Dialect.BIGQUERY:
        cols = ",\n".join(f"  {n} {t}" for n, t, _, _ in COLUMNS)
        risk = ",\n".join(f"  {n} {t}" for n, t, _, _ in RISK_COLUMNS)
        return (
            f"-- BigQuery. Partitioned by event date, clustered for session reads.\n"
            f"CREATE TABLE IF NOT EXISTS `{dataset}.events` (\n{cols}\n)\n"
            f"PARTITION BY DATE(timestamp)\n"
            f"CLUSTER BY session_id, screen_id, event_name;\n\n"
            f"CREATE TABLE IF NOT EXISTS `{dataset}.risk_signals` (\n{risk}\n)\n"
            f"PARTITION BY DATE(timestamp)\n"
            f"CLUSTER BY session_id, kind;\n"
        )

    if dialect is Dialect.CLICKHOUSE:
        cols = ",\n".join(f"  {n} {t}" for n, _, t, _ in COLUMNS)
        risk = ",\n".join(f"  {n} {t}" for n, _, t, _ in RISK_COLUMNS)
        return (
            f"-- ClickHouse. ReplacingMergeTree gives idempotent re-ingest on\n"
            f"-- event_id, which matters because every vendor delivers at-least-once.\n"
            f"CREATE TABLE IF NOT EXISTS {dataset}.events (\n{cols}\n)\n"
            f"ENGINE = ReplacingMergeTree()\n"
            f"PARTITION BY toYYYYMM(timestamp)\n"
            f"ORDER BY (session_id, timestamp, event_id);\n\n"
            f"CREATE TABLE IF NOT EXISTS {dataset}.risk_signals (\n{risk}\n)\n"
            f"ENGINE = ReplacingMergeTree()\n"
            f"PARTITION BY toYYYYMM(timestamp)\n"
            f"ORDER BY (session_id, timestamp, signal_id);\n"
        )

    cols = ",\n".join(f"  {n} {t}" for n, _, _, t in COLUMNS)
    risk = ",\n".join(f"  {n} {t}" for n, _, _, t in RISK_COLUMNS)
    return (
        f"CREATE TABLE IF NOT EXISTS events (\n{cols}\n);\n"
        f"CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp);\n"
        f"CREATE INDEX IF NOT EXISTS idx_events_screen ON events(screen_id);\n\n"
        f"CREATE TABLE IF NOT EXISTS risk_signals (\n{risk}\n);\n"
        f"CREATE INDEX IF NOT EXISTS idx_risk_session ON risk_signals(session_id, timestamp);\n"
    )


# ---------------------------------------------------------------------- store


class EventStore:
    """SQLite-backed reference implementation of the event store.

    Semantics match the warehouse deployment: content-addressed event IDs make
    writes idempotent, and sessions read back in timestamp order.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(generate_ddl(Dialect.SQLITE))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ write

    def insert_events(self, events: Iterable[CanonicalEvent]) -> int:
        """Insert events idempotently. Returns the number of new rows."""
        rows = []
        for ev in events:
            ev = ev.finalised()
            rows.append((
                ev.event_id, ev.event_name, ev.event_type, ev.screen_id,
                ev.screen_tag, ev.session_id, ev.user_id, ev.anonymous_id,
                ev.timestamp.astimezone(timezone.utc).isoformat(),
                ev.received_at.astimezone(timezone.utc).isoformat()
                if ev.received_at else None,
                ev.app_version, ev.platform, ev.source,
                json.dumps(ev.properties, default=str), ev.taxonomy_fingerprint,
            ))
        if not rows:
            return 0
        before = self.count_events()
        placeholders = ",".join("?" * len(COLUMNS))
        names = ",".join(n for n, _, _, _ in COLUMNS)
        with self._tx() as conn:
            conn.executemany(
                f"INSERT OR IGNORE INTO events ({names}) VALUES ({placeholders})", rows
            )
        return self.count_events() - before

    def insert_risk_signals(self, signals: Iterable[Any]) -> int:
        rows = []
        for s in signals:
            rows.append((
                s.signal_id, s.kind, s.session_id, s.user_id,
                s.timestamp.astimezone(timezone.utc).isoformat(),
                1 if s.fatal else 0, s.title, s.screen_id,
                s.position_in_session, s.source,
                json.dumps(s.properties, default=str),
            ))
        if not rows:
            return 0
        before = self.count_risk_signals()
        placeholders = ",".join("?" * len(RISK_COLUMNS))
        names = ",".join(n for n, _, _, _ in RISK_COLUMNS)
        with self._tx() as conn:
            conn.executemany(
                f"INSERT OR IGNORE INTO risk_signals ({names}) VALUES ({placeholders})",
                rows,
            )
        return self.count_risk_signals() - before

    # ------------------------------------------------------------------- read

    def count_events(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def count_risk_signals(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM risk_signals").fetchone()[0]

    def session_ids(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT session_id FROM events "
            "WHERE session_id IS NOT NULL ORDER BY session_id"
        )
        return [r[0] for r in cur.fetchall()]

    def session_events(self, session_id: str) -> list[CanonicalEvent]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp, event_id",
            (session_id,),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    def iter_events(self) -> Iterator[CanonicalEvent]:
        cur = self._conn.execute("SELECT * FROM events ORDER BY timestamp, event_id")
        for row in cur:
            yield self._row_to_event(row)

    def screen_counts(self) -> dict[str, int]:
        cur = self._conn.execute(
            "SELECT screen_id, COUNT(*) FROM events "
            "WHERE screen_id IS NOT NULL GROUP BY screen_id ORDER BY 2 DESC"
        )
        return {r[0]: r[1] for r in cur.fetchall()}

    def orphan_count(self) -> int:
        """Events whose screen tag did not resolve. These vanish from every journey."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE screen_tag IS NOT NULL AND screen_id IS NULL"
        ).fetchone()[0]

    def null_session_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id IS NULL"
        ).fetchone()[0]

    def latest_timestamp(self) -> datetime | None:
        row = self._conn.execute("SELECT MAX(timestamp) FROM events").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=row["event_id"],
            event_name=row["event_name"],
            event_type=row["event_type"] or "interaction",
            screen_id=row["screen_id"],
            screen_tag=row["screen_tag"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            anonymous_id=row["anonymous_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            received_at=datetime.fromisoformat(row["received_at"])
            if row["received_at"] else None,
            app_version=row["app_version"],
            platform=row["platform"],
            source=row["source"] or "unknown",
            properties=json.loads(row["properties"] or "{}"),
            taxonomy_fingerprint=row["taxonomy_fingerprint"],
        )
