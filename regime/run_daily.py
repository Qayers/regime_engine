"""Dzienna orkiestracja (ETAP E6): lockfile → fetch → engine → dashboard.

Krótkotrwały proces odpalany z crona (shared hosting reapuje długo żyjące procesy —
patrz README). Kolejność i odporność:
  1. lock (atomowy O_EXCL; przejęcie gdy przeterminowany) — brak równoległych biegów
  2. fetch prices/macro/events — MIĘKKIE: wyjątek loguje się i NIE zabija biegu
     (engine policzy na tym co jest, a stale_safe wejdzie jeśli źródło score przeterminowane)
  3. engine.run_engine — TWARDY: liczy regime_history + zapisuje stan atomowo
  4. dashboard.render_dashboard — MIĘKKI: błąd nie unieważnia policzonego stanu

Kod wyjścia: 0 = OK (lub inny bieg trzyma lock), 1 = twardy błąd silnika.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

from . import config, dashboard, db, engine, fetch_finnhub, fetch_fred, fetch_tiingo

log = config.get_logger("run_daily")

LOCK_STALE_S = 1800     # 30 min — po tym lock uznajemy za osierocony (zawieszony bieg)

# Fetch pełnej historii (full=True), NIE okna days=N. Kluczowe dla odporności: przy przerwie
# w biegach >N dni (outage / token / brak crona) okno days=N zostawiłoby TRWAŁĄ dziurę w
# prices_eod — korumpuje mom20/percentyle i nigdy się nie zaleczy. Tiingo/FRED zwracają pełną
# historię TYM SAMYM 1 zapytaniem/symbol, więc full jest tak samo tani, a samonaprawiający.


def _acquire_lock() -> bool:
    """Atomowy lock przez O_CREAT|O_EXCL. Przejmuje lock przeterminowany (>LOCK_STALE_S)."""
    lf = config.LOCK_FILE
    lf.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lf), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lf.stat().st_mtime
        except OSError:
            age = 0.0
        if age > LOCK_STALE_S:
            log.warning("Lock przeterminowany (%.0f s) — przejmuję", age)
            try:
                lf.unlink()
            except OSError:
                pass
            return _acquire_lock()
        log.warning("Inny bieg trzyma lock (wiek %.0f s) — wychodzę", age)
        return False


def _release_lock() -> None:
    try:
        config.LOCK_FILE.unlink()
    except OSError:
        pass


def _soft(name: str, fn) -> None:
    """Uruchamia krok fetch/dashboard; wyjątek loguje i połyka (bieg trwa dalej)."""
    try:
        fn()
        log.info("krok %s: OK", name)
    except Exception as e:  # noqa: BLE001 — celowo szeroki, fetch nie może zabić biegu
        log.error("krok %s: wyjątek — %s", name, e)


def run() -> int:
    if not _acquire_lock():
        return 0
    try:
        db.init_db()
        _soft("tiingo", lambda: fetch_tiingo.fetch_tiingo(full=True))
        _soft("fred", lambda: fetch_fred.fetch_fred(full=True))
        _soft("finnhub", fetch_finnhub.fetch_finnhub)

        # TWARDY: engine musi policzyć stan (na danych z bazy; stale_safe pilnuje świeżości)
        eng = engine.run_engine(write_state_file=True)

        _soft("dashboard", dashboard.render_dashboard)

        log.info("RUN_DAILY OK: %d wierszy %s..%s | tryb=%s stale=%s",
                 eng["rows"], eng["first"], eng["last"],
                 eng.get("state_mode"), eng.get("stale_sources"))
        print(f"OK: session={eng['last']} tryb={eng.get('state_mode')} "
              f"stale={eng.get('stale_sources')} ({eng['rows']} sesji)")
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("RUN_DAILY TWARDY BŁĄD: %s", e)
        print(f"BŁĄD: {e}", file=sys.stderr)
        return 1
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(run())
