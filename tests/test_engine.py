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


# --- E4: logika wskaźników / silnika ----------------------------------------

from datetime import date

from regime import engine, indicators


def test_business_days_lag_monday_scenario():
    """Piątek->poniedziałek = 1 dzień roboczy (nie 3 kalendarzowe) — sedno fixu.
    FRED z lagiem publikacji (piątkowa wartość w pon.) NIE może dać stale_safe."""
    assert engine._business_days_lag(date(2026, 7, 3), date(2026, 7, 6)) == 1
    assert engine._business_days_lag(date(2026, 7, 1), date(2026, 7, 3)) == 2
    assert engine._business_days_lag(date(2026, 7, 6), date(2026, 7, 6)) == 0
    assert engine._business_days_lag(date(2026, 7, 7), date(2026, 7, 6)) == 0  # dane wyprzedzają
    # scenariusz poniedziałkowy: FRED=piątek, expected=poniedziałek → 1 bd ≤ próg 3 → NIE stale
    assert engine._business_days_lag(date(2026, 7, 3), date(2026, 7, 6)) <= config.STALE_FRED_MAX_BDAYS


def test_percentile_rank_bounds():
    """percentile_rank: max→~100, min→~0, mediana→~50 (metoda mean)."""
    win = [1, 2, 3, 4, 5]
    assert indicators.percentile_rank(win, 5) == 90.0   # (4 + 0.5) / 5 * 100
    assert indicators.percentile_rank(win, 1) == 10.0
    assert indicators.percentile_rank(win, 3) == 50.0
    assert indicators.percentile_rank([], 1) is None


def test_momentum_and_diff():
    s = [100, 110, 121]
    assert abs(indicators.momentum(s, 1)[2] - 0.1) < 1e-9
    assert indicators.momentum(s, 1)[0] is None
    assert indicators.diff([1, 3, 6], 1)[2] == 3


def test_hysteresis_requires_two_sessions():
    """Zmiana trybu dopiero po HYSTERESIS_SESSIONS kolejnych sesjach w nowej strefie."""
    # score: neutral, potem 1 sesja risk_off (za mało), potem 2 kolejne (zmiana na 2.)
    rows = [
        {"date": "2026-01-01", "score": 50.0},   # neutral (bootstrap)
        {"date": "2026-01-02", "score": 70.0},   # off-zone #1 — jeszcze neutral
        {"date": "2026-01-03", "score": 71.0},   # off-zone #2 — teraz risk_off
        {"date": "2026-01-04", "score": 50.0},   # neutral-zone #1 — jeszcze risk_off
        {"date": "2026-01-05", "score": 50.0},   # neutral-zone #2 — teraz neutral
    ]
    m = engine.apply_modes(rows)
    assert m["2026-01-01"][0] == "neutral"
    assert m["2026-01-02"][0] == "neutral"        # 1 sesja nie wystarcza
    assert m["2026-01-03"][0] == "risk_off"       # 2 sesje → zmiana
    assert m["2026-01-03"][1] == "2026-01-03"     # mode_since = sesja POTWIERDZENIA
    assert m["2026-01-04"][0] == "risk_off"       # powrót: 1 sesja nie wystarcza
    assert m["2026-01-05"][0] == "neutral"        # 2 sesje → zmiana


def test_source_health_preserves_last_good_on_failure(tmp_path, monkeypatch):
    """Niepowodzenie (NULL last_row_date/last_success) NIE kasuje ostatniego dobrego —
    tylko status sie zmienia (diagnostyka na dashboard ma pokazywac last-known-good)."""
    test_db = tmp_path / "t.sqlite3"
    monkeypatch.setattr(config, "DATA_DB", test_db)
    db.init_db()
    with db.get_conn() as conn:
        db.upsert_source_health(conn, "tiingo", "2026-07-07T21:00:00Z", "2026-07-07", "ok")
        db.upsert_source_health(conn, "tiingo", None, None, "rate_limited")
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM source_health WHERE source='tiingo'").fetchone()
    assert r["status"] == "rate_limited"
    assert r["last_row_date"] == "2026-07-07"
    assert r["last_success_utc"] == "2026-07-07T21:00:00Z"
