"""Warstwa SQLite: schemat, inicjalizacja, idempotentne upserty.

Idempotencja przez INSERT ... ON CONFLICT DO UPDATE — wielokrotne uruchomienie
tego samego dnia nie duplikuje danych.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from . import config

# Kolejność DDL bez znaczenia — brak FK między tabelami (świadomie, dla odporności).
SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS prices_eod(
        symbol TEXT NOT NULL,
        date   TEXT NOT NULL,
        open   REAL,
        high   REAL,
        low    REAL,
        close  REAL,
        volume INTEGER,
        PRIMARY KEY(symbol, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_series(
        series TEXT NOT NULL,
        date   TEXT NOT NULL,
        value  REAL,
        PRIMARY KEY(series, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_calendar(
        kind         TEXT NOT NULL,
        symbol       TEXT NOT NULL,
        event_date   TEXT NOT NULL,
        payload_json TEXT,
        fetched_at   TEXT,
        PRIMARY KEY(kind, symbol, event_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS regime_history(
        date           TEXT PRIMARY KEY,
        score          REAL,
        mode           TEXT,
        comp_breadth   REAL,
        comp_credit    REAL,
        comp_vol       REAL,
        comp_rotation  REAL,
        inputs_json    TEXT,
        engine_version TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_health(
        source          TEXT PRIMARY KEY,
        last_success_utc TEXT,
        last_row_date    TEXT,
        status           TEXT
    )
    """,
]


def connect() -> sqlite3.Connection:
    """Otwiera połączenie z bazą (busy_timeout dla współbieżności cron↔dashboard)."""
    conn = sqlite3.connect(str(config.DATA_DB), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Context manager: commit przy sukcesie, zawsze close (bez wycieku FD)."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Tworzy schemat (idempotentnie — CREATE TABLE IF NOT EXISTS)."""
    with get_conn() as conn:
        for ddl in SCHEMA:
            conn.execute(ddl)


# --- upserty (idempotentne) ---------------------------------------------------

def upsert_price(
    conn: sqlite3.Connection,
    symbol: str,
    date: str,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    volume: Optional[int],
) -> None:
    conn.execute(
        """
        INSERT INTO prices_eod(symbol, date, open, high, low, close, volume)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume
        """,
        (symbol, date, open_, high, low, close, volume),
    )


def upsert_macro(
    conn: sqlite3.Connection, series: str, date: str, value: Optional[float]
) -> None:
    conn.execute(
        """
        INSERT INTO macro_series(series, date, value) VALUES(?,?,?)
        ON CONFLICT(series, date) DO UPDATE SET value=excluded.value
        """,
        (series, date, value),
    )


def upsert_event(
    conn: sqlite3.Connection,
    kind: str,
    symbol: str,
    event_date: str,
    payload_json: str,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO event_calendar(kind, symbol, event_date, payload_json, fetched_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(kind, symbol, event_date) DO UPDATE SET
            payload_json=excluded.payload_json, fetched_at=excluded.fetched_at
        """,
        (kind, symbol, event_date, payload_json, fetched_at),
    )


def upsert_regime(
    conn: sqlite3.Connection,
    date: str,
    score: float,
    mode: str,
    comp_breadth: float,
    comp_credit: float,
    comp_vol: float,
    comp_rotation: float,
    inputs_json: str,
    engine_version: str,
) -> None:
    conn.execute(
        """
        INSERT INTO regime_history(
            date, score, mode, comp_breadth, comp_credit, comp_vol,
            comp_rotation, inputs_json, engine_version)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            score=excluded.score, mode=excluded.mode,
            comp_breadth=excluded.comp_breadth, comp_credit=excluded.comp_credit,
            comp_vol=excluded.comp_vol, comp_rotation=excluded.comp_rotation,
            inputs_json=excluded.inputs_json, engine_version=excluded.engine_version
        """,
        (
            date, score, mode, comp_breadth, comp_credit, comp_vol,
            comp_rotation, inputs_json, engine_version,
        ),
    )


def upsert_source_health(
    conn: sqlite3.Connection,
    source: str,
    last_success_utc: Optional[str],
    last_row_date: Optional[str],
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO source_health(source, last_success_utc, last_row_date, status)
        VALUES(?,?,?,?)
        ON CONFLICT(source) DO UPDATE SET
            -- na niepowodzeniu (NULL) zachowaj ostatni znany dobry wiersz/czas; status zawsze świeży
            last_success_utc=COALESCE(excluded.last_success_utc, source_health.last_success_utc),
            last_row_date=COALESCE(excluded.last_row_date, source_health.last_row_date),
            status=excluded.status
        """,
        (source, last_success_utc, last_row_date, status),
    )
