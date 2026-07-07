"""Testy szkieletu E1 — bez sieci. Kolejne etapy dołożą testy logiki (E6).

Uruchomienie: `cd ~/apps/regime_engine && venv/bin/python -m pytest -q`.
"""
from __future__ import annotations

import re

from regime import config, db


def test_engine_version_format():
    """Wersja silnika w formacie semver X.Y.Z."""
    assert re.match(r"^\d+\.\d+\.\d+$", config.ENGINE_VERSION)


def test_weights_sum_to_one():
    """Wagi komponentów score sumują się do 1.0."""
    assert abs(sum(config.WEIGHTS.values()) - 1.0) < 1e-9


def test_core_symbols_present():
    """Cztery symbole strukturalne wchodzące do score są zdefiniowane (tickery Tiingo)."""
    assert config.CORE_SYMBOLS == ["spy", "rsp", "qqq", "iwm"]


def test_init_db_creates_all_tables(tmp_path, monkeypatch):
    """init_db() tworzy komplet 5 tabel schematu (na tymczasowej bazie)."""
    test_db = tmp_path / "t.sqlite3"
    monkeypatch.setattr(config, "DATA_DB", test_db)
    db.init_db()
    with db.get_conn() as conn:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    expected = {
        "prices_eod", "macro_series", "event_calendar",
        "regime_history", "source_health",
    }
    assert expected <= tables


def test_upserts_are_idempotent(tmp_path, monkeypatch):
    """Dwukrotny upsert tego samego klucza nie duplikuje wiersza."""
    test_db = tmp_path / "t.sqlite3"
    monkeypatch.setattr(config, "DATA_DB", test_db)
    db.init_db()
    with db.get_conn() as conn:
        db.upsert_price(conn, "spy.us", "2026-01-02", 1, 2, 0.5, 1.5, 100)
        db.upsert_price(conn, "spy.us", "2026-01-02", 1, 2, 0.5, 1.7, 200)  # ta sama data
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT close, volume FROM prices_eod WHERE symbol='spy.us' AND date='2026-01-02'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["close"] == 1.7 and rows[0]["volume"] == 200  # nadpisane, nie zdublowane
