"""Pobieranie kalendarza wyników + IPO z Finnhub (klucz FINNHUB_API_KEY, darmowy tier).

/calendar/earnings dla EVENT_WATCHLIST (okno -7..+35 dni), /calendar/ipo (+30 dni).
Zapis do event_calendar. W v1 kalendarz jest tylko zbierany i pokazywany na dashboardzie.
Retry 3× (2/4/8 s), upsert (idempotencja), zapis source_health.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from . import config, db

log = config.get_logger("fetch_finnhub")

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _get_json_with_retry(path: str, params: dict) -> Optional[dict]:
    """GET JSON z retry. Zwraca dict albo None. Token przekazywany w params (nie logowany)."""
    url = f"{FINNHUB_BASE}{path}"
    log_params = {k: v for k, v in params.items() if k != "token"}
    last_exc = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            log.warning(
                "Finnhub HTTP %s (próba %d/%d) %s %s",
                r.status_code, attempt + 1, config.HTTP_RETRIES, path, log_params,
            )
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            log.warning(
                "Finnhub wyjątek (próba %d/%d) %s: %s",
                attempt + 1, config.HTTP_RETRIES, path, e,
            )
        if attempt < config.HTTP_RETRIES - 1:
            time.sleep(config.HTTP_BACKOFF_S[attempt])
    if last_exc:
        log.error("Finnhub nieudane po %d próbach: %s", config.HTTP_RETRIES, last_exc)
    return None


def _valid_date(d: str) -> bool:
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def fetch_finnhub(watchlist: Optional[list[str]] = None) -> dict:
    """Pobiera earnings (per ticker) + IPO, upsert do event_calendar, source_health.

    Zwraca {"earnings": {...}, "ipo": {...}} ze zliczeniami i zakresami dat do raportu.
    """
    watchlist = watchlist or config.EVENT_WATCHLIST
    if not config.FINNHUB_API_KEY:
        log.error("FINNHUB_API_KEY pusty — pomijam Finnhub (uzupełnij .env)")
        with db.get_conn() as conn:
            db.upsert_source_health(conn, "finnhub", None, None, "error_no_key")
        return {
            "earnings": {"rows": 0, "min_date": None, "max_date": None, "status": "no_key"},
            "ipo": {"rows": 0, "min_date": None, "max_date": None, "status": "no_key"},
        }

    today = datetime.now(timezone.utc).date()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = config.FINNHUB_API_KEY
    all_dates: list[str] = []
    any_ok = False

    # --- earnings (per ticker) ---
    e_rows = 0
    e_dates: list[str] = []
    e_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    e_to = (today + timedelta(days=35)).strftime("%Y-%m-%d")
    for sym in watchlist:
        data = _get_json_with_retry(
            "/calendar/earnings",
            {"from": e_from, "to": e_to, "symbol": sym, "token": token},
        )
        if data is None:
            continue
        any_ok = True
        for rec in data.get("earningsCalendar", []) or []:
            ed = (rec.get("date") or "").strip()
            rsym = (rec.get("symbol") or sym).strip().upper()
            if not _valid_date(ed):
                continue
            with db.get_conn() as conn:
                db.upsert_event(conn, "earnings", rsym, ed, json.dumps(rec), fetched_at)
            e_rows += 1
            e_dates.append(ed)
        time.sleep(0.3)  # darmowy tier ~60 zapytań/min
    log.info("Finnhub earnings: %d wierszy dla %d tickerów", e_rows, len(watchlist))

    # --- IPO (najbliższe 30 dni) ---
    i_rows = 0
    i_dates: list[str] = []
    i_from = today.strftime("%Y-%m-%d")
    i_to = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    data = _get_json_with_retry("/calendar/ipo", {"from": i_from, "to": i_to, "token": token})
    if data is not None:
        any_ok = True
        for rec in data.get("ipoCalendar", []) or []:
            idt = (rec.get("date") or "").strip()
            # symbol IPO bywa pusty — fallback na nazwę (PK to kind+symbol+date)
            isym = (rec.get("symbol") or rec.get("name") or "?").strip().upper()[:32]
            if not _valid_date(idt):
                continue
            with db.get_conn() as conn:
                db.upsert_event(conn, "ipo", isym, idt, json.dumps(rec), fetched_at)
            i_rows += 1
            i_dates.append(idt)
    log.info("Finnhub IPO: %d wierszy", i_rows)

    all_dates = e_dates + i_dates
    global_max = max(all_dates) if all_dates else None
    with db.get_conn() as conn:
        db.upsert_source_health(
            conn,
            "finnhub",
            fetched_at if any_ok else None,
            global_max,
            "ok" if any_ok else "error",
        )

    return {
        "earnings": {
            "rows": e_rows,
            "min_date": min(e_dates) if e_dates else None,
            "max_date": max(e_dates) if e_dates else None,
            "status": "ok" if e_rows else ("empty" if any_ok else "error"),
        },
        "ipo": {
            "rows": i_rows,
            "min_date": min(i_dates) if i_dates else None,
            "max_date": max(i_dates) if i_dates else None,
            "status": "ok" if i_rows else ("empty" if any_ok else "error"),
        },
    }


if __name__ == "__main__":
    res = fetch_finnhub()
    for kind, info in res.items():
        print(f"{kind:9s} {info['status']:8s} rows={info['rows']:>5} "
              f"{info['min_date']}..{info['max_date']}")
