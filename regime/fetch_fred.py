"""Pobieranie serii makro z FRED API (klucz FRED_API_KEY).

Serie: BAMLH0A0HYM2 (HY OAS), VIXCLS (VIX) → score; DGS10, BAMLC0A0CM → tylko zapis.
FRED publikuje z opóźnieniem ~1 dzień — pobieramy wszystkie dostępne obserwacje w oknie,
a logikę „ostatniej dostępnej" realizuje silnik (E4). Wartości "." (brak) pomijamy.
Retry 3× (2/4/8 s), upsert do macro_series, zapis source_health.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from . import config, db

log = config.get_logger("fetch_fred")

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _get_json_with_retry(params: dict) -> Optional[dict]:
    """GET JSON z retry. Zwraca dict albo None (błąd/wyczerpane próby). Nie loguje klucza."""
    safe = params.get("series_id")
    last_exc = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = requests.get(FRED_URL, params=params, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            # 400/403 zwykle = zły klucz; retry i tak nie pomoże, ale zachowujemy schemat
            log.warning(
                "FRED HTTP %s (próba %d/%d) series=%s",
                r.status_code, attempt + 1, config.HTTP_RETRIES, safe,
            )
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            log.warning(
                "FRED wyjątek (próba %d/%d) series=%s: %s",
                attempt + 1, config.HTTP_RETRIES, safe, e,
            )
        if attempt < config.HTTP_RETRIES - 1:
            time.sleep(config.HTTP_BACKOFF_S[attempt])
    if last_exc:
        log.error("FRED nieudane po %d próbach: %s", config.HTTP_RETRIES, last_exc)
    return None


BACKFILL_START = "2024-01-02"  # domyślny początek historii (E3)


def fetch_fred(
    series: Optional[list[str]] = None,
    days: Optional[int] = None,
    full: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    update_health: bool = True,
) -> dict:
    """Pobiera serie FRED, upsert do macro_series, aktualizuje source_health.

    Okno dat (priorytet): jawne start_date/end_date → full (od 2024-01-02) → days wstecz.
    update_health=False pomija zapis source_health (dla chunków backfillu — pisze wołający).
    Zwraca {series: {"rows", "min_date", "max_date", "status"}} do raportu.
    """
    series = series or config.FRED_SERIES_ALL
    if not config.FRED_API_KEY:
        log.error("FRED_API_KEY pusty — pomijam FRED (uzupełnij .env)")
        if update_health:
            with db.get_conn() as conn:
                db.upsert_source_health(conn, "fred", None, None, "error_no_key")
        return {s: {"rows": 0, "min_date": None, "max_date": None, "status": "no_key"} for s in series}

    # okno obserwacji wspólne dla wszystkich serii w tym wywołaniu
    obs_end = end_date or datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
    if start_date:
        obs_start = start_date
    elif full:
        obs_start = BACKFILL_START
    else:
        end_dt = datetime.strptime(obs_end, "%Y-%m-%d").date()
        obs_start = (end_dt - timedelta(days=days or 60)).strftime("%Y-%m-%d")

    summary: dict[str, dict] = {}
    any_ok = False
    global_max_date: Optional[str] = None

    for s in series:
        params = {
            "series_id": s,
            "api_key": config.FRED_API_KEY,
            "file_type": "json",
            "observation_start": obs_start,
            "observation_end": obs_end,
        }

        data = _get_json_with_retry(params)
        if data is None or "observations" not in data:
            summary[s] = {"rows": 0, "min_date": None, "max_date": None, "status": "error"}
            log.warning("FRED %s: brak obserwacji w odpowiedzi", s)
            continue

        rows = []
        for obs in data["observations"]:
            val = (obs.get("value") or "").strip()
            date = (obs.get("date") or "").strip()
            if val in ("", ".") or not date:  # brak wartości → pomijamy
                continue
            try:
                value = float(val)
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                continue
            rows.append((date, value))

        if not rows:
            summary[s] = {"rows": 0, "min_date": None, "max_date": None, "status": "no_data"}
            log.warning("FRED %s: brak liczbowych obserwacji w oknie", s)
            continue

        with db.get_conn() as conn:
            for date, value in rows:
                db.upsert_macro(conn, s, date, value)
        dates = [r[0] for r in rows]
        mn, mx = min(dates), max(dates)
        summary[s] = {"rows": len(rows), "min_date": mn, "max_date": mx, "status": "ok"}
        any_ok = True
        if global_max_date is None or mx > global_max_date:
            global_max_date = mx
        log.info("FRED %s: %d obserwacji %s..%s", s, len(rows), mn, mx)
        time.sleep(0.4)

    if update_health:
        with db.get_conn() as conn:
            db.upsert_source_health(
                conn,
                "fred",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if any_ok else None,
                global_max_date,
                "ok" if any_ok else "error",
            )
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch FRED (test ręczny)")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    res = fetch_fred(days=args.days, full=args.full)
    for s, info in res.items():
        print(f"{s:14s} {info['status']:10s} rows={info['rows']:>5} "
              f"{info['min_date']}..{info['max_date']}")
