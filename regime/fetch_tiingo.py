"""Pobieranie EOD z Tiingo (klucz TIINGO_API_KEY) — ZASTĄPIŁO Stooq 2026-07-07.

Powód zmiany: Stooq postawił ścianę anti-bot (JS proof-of-work + blokada IP datacenter),
CSV niedostępny programowo z tego hosta (potwierdzone). Tiingo: JSON, pełna historia
jednym zapytaniem per symbol, ceny SKORYGOWANE (adj*) — właściwe do momentum/ratio/zwrotów.

Endpoint: GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate&endDate
Auth: nagłówek 'Authorization: Token <klucz>' (token poza URL i logami).
Retry 3× (2/4/8 s), upsert do prices_eod (adj* → kolumny OHLC), zapis source_health.
Awaria/brak jednego symbolu NIE przerywa reszty.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from . import config, db

log = config.get_logger("fetch_tiingo")

TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
BACKFILL_START = "2024-01-02"  # domyślny początek historii (E3)

# Sentinel: limit zapytań (HTTP 429). Odrębny od None (=błąd) — bo retry NIE ma sensu
# przy limicie godzinowym (spalałby limit szybciej), a kolejne symbole też dostaną 429.
RATE_LIMITED = "__RATE_LIMITED__"


def _get_json_with_retry(url: str, headers: dict, params: dict, ticker: str):
    """GET JSON z retry. Zwraca JSON | RATE_LIMITED (429, bez retry) | None (błąd). Nie loguje tokenu."""
    last_exc = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                # limit — przerwij OD RAZU (retry tylko szybciej spala limit)
                log.warning("Tiingo HTTP 429 (limit) ticker=%s — przerywam bez retry", ticker)
                return RATE_LIMITED
            log.warning(
                "Tiingo HTTP %s (próba %d/%d) ticker=%s",
                r.status_code, attempt + 1, config.HTTP_RETRIES, ticker,
            )
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            log.warning(
                "Tiingo wyjątek (próba %d/%d) ticker=%s: %s",
                attempt + 1, config.HTTP_RETRIES, ticker, e,
            )
        if attempt < config.HTTP_RETRIES - 1:
            time.sleep(config.HTTP_BACKOFF_S[attempt])
    if last_exc:
        log.error("Tiingo nieudane po %d próbach: %s", config.HTTP_RETRIES, last_exc)
    return None


def _pick(rec: dict, adj_key: str, raw_key: str) -> Optional[float]:
    """Preferuj wartość skorygowaną (adj*), fallback surowa."""
    v = rec.get(adj_key)
    if v is None:
        v = rec.get(raw_key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_tiingo(
    symbols: Optional[list[str]] = None,
    days: Optional[int] = None,
    full: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session_date=None,
    update_health: bool = True,
) -> dict:
    """Pobiera EOD dla symboli, upsert do prices_eod, aktualizuje source_health.

    Okno dat (priorytet): jawne start_date/end_date → full (od 2024-01-02) → days wstecz.
    update_health=False pomija zapis source_health (dla chunków backfillu — pisze wołający).
    Zwraca {symbol: {"rows", "min_date", "max_date", "status"}} do raportu.
    """
    symbols = symbols or config.ALL_PRICE_SYMBOLS
    if not config.TIINGO_API_KEY:
        log.error("TIINGO_API_KEY pusty — pomijam ceny (uzupełnij .env)")
        if update_health:
            with db.get_conn() as conn:
                db.upsert_source_health(conn, "tiingo", None, None, "error_no_key")
        return {s: {"rows": 0, "min_date": None, "max_date": None, "status": "no_key"} for s in symbols}

    headers = {"Authorization": f"Token {config.TIINGO_API_KEY}"}
    if end_date:
        end = end_date
    else:
        end = (session_date or datetime.now(timezone.utc).date()).strftime("%Y-%m-%d")
    if start_date:
        start = start_date
    elif full:
        start = BACKFILL_START
    else:
        end_dt = datetime.strptime(end, "%Y-%m-%d").date()
        start = (end_dt - timedelta(days=days or 40)).strftime("%Y-%m-%d")

    summary: dict[str, dict] = {}
    any_ok = False
    rate_limited = False
    global_max_date: Optional[str] = None

    for idx, sym in enumerate(symbols):
        url = TIINGO_URL.format(ticker=sym.upper())
        params = {"startDate": start, "endDate": end, "format": "json"}
        data = _get_json_with_retry(url, headers, params, sym)
        if data == RATE_LIMITED:
            # limit godzinowy — reszta symboli też dostanie 429; oznacz i przerwij
            rate_limited = True
            for rest in symbols[idx:]:
                summary[rest] = {"rows": 0, "min_date": None, "max_date": None, "status": "rate_limited"}
            log.warning("Tiingo limit — przerywam po %s, pominięto %d symboli", sym, len(symbols) - idx - 1)
            break
        if data is None:
            summary[sym] = {"rows": 0, "min_date": None, "max_date": None, "status": "error"}
            continue
        if not isinstance(data, list) or not data:
            summary[sym] = {"rows": 0, "min_date": None, "max_date": None, "status": "no_data"}
            log.warning("Tiingo %s: pusta odpowiedź (symbol nie istnieje?) — pomijam", sym)
            continue

        rows = []
        for rec in data:
            date = (rec.get("date") or "")[:10]
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                continue
            close = _pick(rec, "adjClose", "close")
            if close is None:
                continue
            o = _pick(rec, "adjOpen", "open")
            h = _pick(rec, "adjHigh", "high")
            low = _pick(rec, "adjLow", "low")
            vol = rec.get("volume")
            try:
                vol = int(vol) if vol is not None else None
            except (TypeError, ValueError):
                vol = None
            rows.append((date, o, h, low, close, vol))

        if not rows:
            summary[sym] = {"rows": 0, "min_date": None, "max_date": None, "status": "no_data"}
            log.warning("Tiingo %s: brak poprawnych wierszy", sym)
            continue

        with db.get_conn() as conn:
            for row in rows:
                db.upsert_price(conn, sym, *row)
        dates = [r[0] for r in rows]
        mn, mx = min(dates), max(dates)
        summary[sym] = {"rows": len(rows), "min_date": mn, "max_date": mx, "status": "ok"}
        any_ok = True
        if global_max_date is None or mx > global_max_date:
            global_max_date = mx
        log.info("Tiingo %s: %d wierszy %s..%s", sym, len(rows), mn, mx)
        time.sleep(0.4)

    # source_health = DIAGNOSTYKA na dashboard (NIE wejście do stale_safe — to liczy
    # engine z MAX(date) per symbol CORE bezpośrednio z tabeli). status rozróżnia limit.
    status = "ok" if any_ok else ("rate_limited" if rate_limited else "error")
    if update_health:
        with db.get_conn() as conn:
            db.upsert_source_health(
                conn,
                "tiingo",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if any_ok else None,
                global_max_date,
                status,
            )
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch Tiingo EOD (test ręczny)")
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--start", default=None, help="start_date dla --full (YYYY-MM-DD)")
    args = ap.parse_args()
    res = fetch_tiingo(days=args.days, full=args.full, start_date=args.start)
    for s, info in res.items():
        print(f"{s:6s} {info['status']:8s} rows={info['rows']:>5} "
              f"{info['min_date']}..{info['max_date']}")
