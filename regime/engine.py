"""Silnik: komponenty, score, tryb + histereza + stale_safe. Zapis regime_history + stanu.

Score = 0.30·breadth + 0.30·credit + 0.25·vol + 0.15·rotation (komponenty 0..100, 100=stres).
Tryby: risk_on/neutral/risk_off; histereza HYSTERESIS_SESSIONS sesji; wymuszenie stale_safe.

FIX #2 (wymóg usera 2026-07-07): świeżość (stale_safe) liczona z MAX(date) per KAŻDY
z 4 symboli CORE i 2 serii FRED_SCORE BEZPOŚREDNIO z tabel danych — NIE z source_health
(tam any_ok maskuje padnięcie symbolu CORE gdy uda się choćby jeden watchowy). source_health
zostaje wyłącznie jako diagnostyka na dashboard.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

from . import config, db, indicators, state

log = config.get_logger("engine")

FRED_HY = "BAMLH0A0HYM2"
FRED_VIX = "VIXCLS"
FRED_SCORE = (FRED_HY, FRED_VIX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- wczytanie danych ---------------------------------------------------------

def _load_prices() -> tuple[list[str], dict]:
    """Zwraca (dates, closes): dates = daty z KOMPLETEM 4 symboli CORE; closes = dict
    symbol->list[close] wyrównany do dates. Watch symbole nie wchodzą do score."""
    core = config.CORE_SYMBOLS
    per = {s: {} for s in core}
    with db.get_conn() as c:
        for s in core:
            for r in c.execute("SELECT date, close FROM prices_eod WHERE symbol=? ORDER BY date", (s,)):
                if r["close"] is not None:
                    per[s][r["date"]] = float(r["close"])
    common = sorted(set.intersection(*[set(per[s].keys()) for s in core])) if all(per.values()) else []
    closes = {s: [per[s][d] for d in common] for s in core}
    return common, closes


def _load_macro_asof(dates: list[str]) -> dict:
    """Dla każdej daty sesji: as-of (ostatnia dostępna <= data) HY_OAS i VIX.
    FRED publikuje z opóźnieniem → 'ostatnia dostępna' zgodnie ze spec."""
    out = {}
    with db.get_conn() as c:
        for series in FRED_SCORE:
            rows = c.execute(
                "SELECT date, value FROM macro_series WHERE series=? AND value IS NOT NULL ORDER BY date",
                (series,),
            ).fetchall()
            mdates = [r["date"] for r in rows]
            mvals = [float(r["value"]) for r in rows]
            asof = []
            for d in dates:
                i = bisect_right(mdates, d) - 1
                asof.append(mvals[i] if i >= 0 else None)
            out[series] = asof
    return out


# --- obliczenia komponentów ---------------------------------------------------

def compute_rows(dates: list[str], closes: dict, macro: dict) -> list[dict]:
    """Liczy komponenty i score dla każdej daty. Zwraca listę dict (tylko z pełnym score)."""
    spy, rsp, qqq, iwm = closes["spy"], closes["rsp"], closes["qqq"], closes["iwm"]
    hy, vix = macro[FRED_HY], macro[FRED_VIX]
    W = config.PERCENTILE_WINDOW
    n = len(dates)

    # Szerokość: ratio RSP/SPY, mom20; komponent = percentyl(-mom20) (zwężanie → stres)
    ratio = [rsp[i] / spy[i] if spy[i] else None for i in range(n)]
    neg_mom = [(-m if m is not None else None) for m in indicators.momentum(ratio, 20)]
    breadth, w_b = indicators.rolling_percentile(neg_mom, W)

    # Kredyt: 0.5·percentyl(HY) + 0.5·percentyl(HY zmiana 20d)
    lvl, w_l = indicators.rolling_percentile(hy, W)
    chg, w_c = indicators.rolling_percentile(indicators.diff(hy, 20), W)
    credit = indicators.combine_half(lvl, chg)

    # Zmienność: 0.5·percentyl(VIX) + 0.5·percentyl(rv5 QQQ)
    vixp, w_v = indicators.rolling_percentile(vix, W)
    rvp, w_r = indicators.rolling_percentile(indicators.realized_vol(qqq, 5), W)
    vol = indicators.combine_half(vixp, rvp)

    # Rotacja: percentyl(slope20 z ln(IWM)-ln(QQQ))
    spread = [(math.log(iwm[i]) - math.log(qqq[i])) if (iwm[i] > 0 and qqq[i] > 0) else None for i in range(n)]
    rot, w_ro = indicators.rolling_percentile(indicators.linreg_slope(spread, 20), W)

    Wt = config.WEIGHTS
    rows = []
    for i, d in enumerate(dates):
        c = {"breadth": breadth[i], "credit": credit[i], "vol": vol[i], "rotation": rot[i]}
        if any(v is None for v in c.values()):
            continue
        score = (Wt["breadth"] * c["breadth"] + Wt["credit"] * c["credit"]
                 + Wt["vol"] * c["vol"] + Wt["rotation"] * c["rotation"])
        warmup = any([w_b[i], w_l[i], w_c[i], w_v[i], w_r[i], w_ro[i]])
        rows.append({
            "date": d, "score": round(score, 2),
            "breadth": round(c["breadth"], 2), "credit": round(c["credit"], 2),
            "vol": round(c["vol"], 2), "rotation": round(c["rotation"], 2),
            "inputs": {"spy": spy[i], "rsp": rsp[i], "qqq": qqq[i], "iwm": iwm[i],
                       "hy_oas": hy[i], "vix": vix[i], "warmup": warmup},
        })
    return rows


# --- tryby + histereza --------------------------------------------------------

def _zone(score: float) -> str:
    if score >= config.RISK_OFF_TH:
        return "risk_off"
    if score <= config.RISK_ON_TH:
        return "risk_on"
    return "neutral"


def apply_modes(rows: list[dict]) -> dict:
    """Sekwencyjnie przypisuje tryb z histerezą: zmiana wymaga HYSTERESIS_SESSIONS
    kolejnych sesji w tej samej strefie (anty-flapping). Zwraca date -> (mode, mode_since)."""
    hyst = config.HYSTERESIS_SESSIONS
    out = {}
    cur = since = prev_zone = None
    run = 0
    for r in rows:
        z = _zone(r["score"])
        if z == prev_zone:
            run += 1
        else:
            run, prev_zone = 1, z
        if cur is None:                       # bootstrap: pierwsza sesja = jej strefa
            cur, since = z, r["date"]
        elif z != cur and run >= hyst:        # zmiana dopiero po `hyst` sesjach w nowej strefie
            cur, since = z, r["date"]
        out[r["date"]] = (cur, since)
    return out


# --- świeżość (stale_safe) — FIX #2: z tabel danych, nie z source_health -------

def _expected_session_date() -> date:
    """Oczekiwana najnowsza sesja US = ostatni dzień roboczy <= dziś (run po zamknięciu US)."""
    d = datetime.now(timezone.utc).date()
    while d.weekday() >= 5:  # 5=sob, 6=niedz
        d -= timedelta(days=1)
    return d


def check_stale(expected: date) -> list[str]:
    """Lista przeterminowanych źródeł SCORE (>STALE_HOURS względem oczekiwanej sesji).
    MAX(date) per symbol CORE + serię FRED_SCORE BEZPOŚREDNIO z tabel."""
    stale = []
    with db.get_conn() as c:
        for s in config.CORE_SYMBOLS:
            mx = c.execute("SELECT MAX(date) FROM prices_eod WHERE symbol=?", (s,)).fetchone()[0]
            if mx is None or (expected - date.fromisoformat(mx)).days * 24 > config.STALE_HOURS:
                stale.append(f"price:{s}")
        for series in FRED_SCORE:
            mx = c.execute(
                "SELECT MAX(date) FROM macro_series WHERE series=? AND value IS NOT NULL", (series,)
            ).fetchone()[0]
            if mx is None or (expected - date.fromisoformat(mx)).days * 24 > config.STALE_HOURS:
                stale.append(f"macro:{series}")
    return stale


# --- budowa stanu -------------------------------------------------------------

def _next_events(session_date: str, days: int) -> list[dict]:
    end = (date.fromisoformat(session_date) + timedelta(days=days)).isoformat()
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT kind, symbol, event_date FROM event_calendar "
            "WHERE event_date >= ? AND event_date <= ? ORDER BY event_date, kind, symbol",
            (session_date, end),
        ).fetchall()
    return [{"kind": r["kind"], "symbol": r["symbol"], "date": r["event_date"]} for r in rows]


def _finnhub_health() -> str:
    with db.get_conn() as c:
        r = c.execute("SELECT status FROM source_health WHERE source='finnhub'").fetchone()
    return r["status"] if r else "unknown"


def build_state(last_row: dict, mode: str, since: str, stale_sources: list[str]) -> dict:
    """Buduje payload regime_state.json. stale_safe wymusza tryb niezależnie od score."""
    forced = "stale_safe" if stale_sources else mode
    tiingo_stale = any(s.startswith("price:") for s in stale_sources)
    fred_stale = any(s.startswith("macro:") for s in stale_sources)
    return {
        "schema_version": config.SCHEMA_VERSION,
        "engine_version": config.ENGINE_VERSION,
        "generated_at_utc": _now_iso(),
        "session_date": last_row["date"],
        "score": last_row["score"],
        "mode": forced,
        "mode_since": last_row["date"] if stale_sources else since,
        "components": {
            "breadth": last_row["breadth"], "credit": last_row["credit"],
            "vol": last_row["vol"], "rotation": last_row["rotation"],
        },
        "freshness": {  # diagnostyka; tiingo/fred z reguły stale_safe, finnhub z source_health
            "tiingo": "stale" if tiingo_stale else "ok",
            "fred": "stale" if fred_stale else "ok",
            "finnhub": _finnhub_health(),
        },
        "stale_sources": stale_sources,
        "next_events_7d": _next_events(last_row["date"], 7),
    }


# --- orkiestracja -------------------------------------------------------------

def run_engine(write_state_file: bool = True) -> dict:
    """Liczy pełną historię regime_history i (opcjonalnie) zapisuje bieżący stan."""
    dates, closes = _load_prices()
    if len(dates) < 2 * 20:
        raise RuntimeError(f"za mało sesji z kompletem CORE: {len(dates)}")
    macro = _load_macro_asof(dates)
    rows = compute_rows(dates, closes, macro)
    if not rows:
        raise RuntimeError("brak policzalnych wierszy score")
    modes = apply_modes(rows)

    with db.get_conn() as c:
        for r in rows:
            m, since = modes[r["date"]]
            db.upsert_regime(c, r["date"], r["score"], m, r["breadth"], r["credit"],
                             r["vol"], r["rotation"], json.dumps(r["inputs"]), config.ENGINE_VERSION)

    result = {"rows": len(rows), "first": rows[0]["date"], "last": rows[-1]["date"]}
    if write_state_file:
        last = rows[-1]
        m, since = modes[last["date"]]
        stale = check_stale(_expected_session_date())
        payload = build_state(last, m, since, stale)
        state.write_state(payload)
        result["state_mode"] = payload["mode"]
        result["stale_sources"] = stale
    log.info("ENGINE: %d wierszy %s..%s | tryb=%s stale=%s",
             result["rows"], result["first"], result["last"],
             result.get("state_mode"), result.get("stale_sources"))
    return result


if __name__ == "__main__":
    db.init_db()
    r = run_engine()
    print(f"regime_history: {r['rows']} wierszy {r['first']}..{r['last']}")
    print(f"stan bieżący: tryb={r.get('state_mode')} stale_sources={r.get('stale_sources')}")
