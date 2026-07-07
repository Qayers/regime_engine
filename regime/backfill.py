"""Przeliczenie historii cen + makro od zadanej daty.

backfill.py --from 2024-01-02 [--to YYYY-MM-DD]

Tiingo i FRED zwracają PEŁNĄ historię jednym zapytaniem na symbol/serię, więc backfill
robi 9 (ceny) + 4 (makro) = 13 zapytań łącznie — bez chunkowania dat. Próba chunkowania
(spec: --chunk-days) okazała się PRZECIWSKUTECZNA: 11 okien × 9 symboli = 99 zapytań
przekroczyło darmowy limit Tiingo ~50/godz (HTTP 429). Parametr --chunk-days zostawiony
jako akceptowany-ale-ignorowany dla zgodności wywołań (patrz log ostrzeżenia).
Kalendarz zdarzeń (Finnhub) jest przyszłościowy — NIE backfillujemy go tutaj.

E4 dołoży sekwencyjne liczenie regime_history po datach (histereza jak w produkcji).
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timezone

from . import config, db
from . import fetch_fred, fetch_tiingo

log = config.get_logger("backfill")

BACKFILL_START = "2024-01-02"
REF_SYMBOL = "spy"  # symbol referencyjny do liczenia sesji i wykrywania braków


def run_backfill(from_d: date, to_d: date) -> dict:
    """Pobiera ceny (Tiingo) + makro (FRED) — pełny zakres jednym zapytaniem/symbol."""
    fs, ts = from_d.strftime("%Y-%m-%d"), to_d.strftime("%Y-%m-%d")
    log.info("BACKFILL start %s..%s | 9 cen + 4 makro = 13 zapytań (bez chunkowania)", fs, ts)
    t0 = time.time()
    pr = fetch_tiingo.fetch_tiingo(start_date=fs, end_date=ts, update_health=True)
    mr = fetch_fred.fetch_fred(start_date=fs, end_date=ts, update_health=True)
    price_errs = sum(1 for v in pr.values() if v["status"] not in ("ok", "no_data"))
    macro_errs = sum(1 for v in mr.values() if v["status"] not in ("ok", "no_data"))
    elapsed = time.time() - t0
    log.info("BACKFILL koniec | czas %.1fs | błędy cen=%d makro=%d", elapsed, price_errs, macro_errs)
    return {"elapsed_s": elapsed, "price_errs": price_errs, "macro_errs": macro_errs,
            "price_summary": pr, "macro_summary": mr}


def report(from_d: date, to_d: date) -> None:
    """Raport E3: liczba sesji, braki danych (per symbol + luki kalendarzowe), zakresy."""
    fs, ts = from_d.strftime("%Y-%m-%d"), to_d.strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM prices_eod WHERE symbol=? AND date BETWEEN ? AND ?",
            (REF_SYMBOL, fs, ts),
        ).fetchone()[0]
        psym = conn.execute(
            "SELECT symbol, COUNT(*) n, MIN(date) mn, MAX(date) mx FROM prices_eod GROUP BY symbol ORDER BY symbol"
        ).fetchall()
        ref_dates = [r[0] for r in conn.execute(
            "SELECT date FROM prices_eod WHERE symbol=? ORDER BY date", (REF_SYMBOL,)
        ).fetchall()]
        macro = conn.execute(
            "SELECT series, COUNT(*) n, MIN(date) mn, MAX(date) mx FROM macro_series GROUP BY series ORDER BY series"
        ).fetchall()

    ref_n = next((r["n"] for r in psym if r["symbol"] == REF_SYMBOL), 0)

    print("=" * 76)
    print(f"BACKFILL RAPORT  zakres {fs}..{ts}")
    print(f"  Sesje handlowe (wg {REF_SYMBOL}): {sessions}")
    print("-" * 76)
    print(f"  CENY per symbol (odchylenie od {REF_SYMBOL}={ref_n} sygnalizuje braki):")
    print(f"    {'symbol':8s} {'rows':>5s} {'delta':>6s}  {'min_date':12s} {'max_date':12s}")
    for r in psym:
        delta = r["n"] - ref_n
        flag = "" if delta == 0 else "  <-- BRAKI" if delta < 0 else "  <-- nadmiar"
        print(f"    {r['symbol']:8s} {r['n']:>5} {delta:>+6}  {r['mn']:12s} {r['mx']:12s}{flag}")
    print("-" * 76)
    print("  MAKRO per serię:")
    print(f"    {'series':14s} {'rows':>5s}  {'min_date':12s} {'max_date':12s}")
    for r in macro:
        print(f"    {r['series']:14s} {r['n']:>5}  {r['mn']:12s} {r['mx']:12s}")
    print("-" * 76)
    # luki kalendarzowe w serii referencyjnej: >4 dni między kolejnymi sesjami
    # (weekend = max 3 dni Pt->Pon; >4 = potencjalny brak lub długie święto — do przeglądu)
    gaps = []
    for a, b in zip(ref_dates, ref_dates[1:]):
        d = (date.fromisoformat(b) - date.fromisoformat(a)).days
        if d > 4:
            gaps.append((a, b, d))
    if gaps:
        print(f"  Luki kalendarzowe w {REF_SYMBOL} (>4 dni — przejrzyj, mogą być długie święta):")
        for a, b, d in gaps:
            print(f"    {a} -> {b}  ({d} dni)")
    else:
        print(f"  Luki kalendarzowe w {REF_SYMBOL}: brak (>4 dni)")
    print("=" * 76)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill cen (Tiingo) + makro (FRED)")
    ap.add_argument("--from", dest="from_date", default=BACKFILL_START)
    ap.add_argument("--to", dest="to_date", default=None)
    ap.add_argument("--chunk-days", type=int, default=None,
                    help="IGNOROWANY (Tiingo/FRED: pełna historia 1 zapytaniem; chunkowanie = 429)")
    args = ap.parse_args()
    if args.chunk_days is not None:
        log.warning("--chunk-days=%s ZIGNOROWANY (pełny zakres 1 zapytaniem, uniknięcie 429 Tiingo)",
                    args.chunk_days)

    from_d = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    to_d = (datetime.strptime(args.to_date, "%Y-%m-%d").date()
            if args.to_date else datetime.now(timezone.utc).date())

    db.init_db()
    m = run_backfill(from_d, to_d)
    print(f"\nBackfill: {m['elapsed_s']:.1f}s, błędy cen={m['price_errs']} makro={m['macro_errs']}\n")
    report(from_d, to_d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
