"""Wskaźniki w czystym Pythonie: percentyl kroczący, momentum, diff, realized vol, nachylenie.

Konwencja score: 100 = maksymalny stres (risk-off), 0 = pełny spokój (risk-on).
Wszystkie funkcje zwracają listy równej długości z wejściem; None = wartość niepoliczalna
(za mało historii / brak danych). Percentyle liczone metodą 'mean' (rank uśredniony dla remisów).
"""
from __future__ import annotations

import math
from typing import Optional


def percentile_rank(window_values: list[float], x: float) -> Optional[float]:
    """Percentyl wartości x wśród window_values (0..100). 100 = x najwyższy w oknie.
    Metoda mean: (liczba < x + 0.5 * liczba == x) / n * 100."""
    n = len(window_values)
    if n == 0:
        return None
    less = sum(1 for v in window_values if v < x)
    equal = sum(1 for v in window_values if v == x)
    return 100.0 * (less + 0.5 * equal) / n


def rolling_percentile(series: list[Optional[float]], window: int) -> tuple[list, list]:
    """Dla każdego t: percentyl series[t] wśród niepustych wartości okna trailing
    [t-window+1 .. t]. Zwraca (pct, warmup) — warmup=True gdy w oknie < `window` wartości."""
    out: list[Optional[float]] = []
    warm: list[bool] = []
    for t in range(len(series)):
        if series[t] is None:
            out.append(None)
            warm.append(True)
            continue
        lo = max(0, t - window + 1)
        win = [v for v in series[lo:t + 1] if v is not None]
        out.append(percentile_rank(win, series[t]))
        warm.append(len(win) < window)
    return out, warm


def momentum(series: list[Optional[float]], lag: int) -> list[Optional[float]]:
    """series[t]/series[t-lag] - 1. None gdy t<lag lub dzielnik niepoprawny."""
    out: list[Optional[float]] = []
    for t in range(len(series)):
        if t < lag or series[t] is None or series[t - lag] is None or series[t - lag] <= 0:
            out.append(None)
        else:
            out.append(series[t] / series[t - lag] - 1.0)
    return out


def diff(series: list[Optional[float]], lag: int) -> list[Optional[float]]:
    """series[t] - series[t-lag]. None gdy t<lag lub brak danych."""
    out: list[Optional[float]] = []
    for t in range(len(series)):
        if t < lag or series[t] is None or series[t - lag] is None:
            out.append(None)
        else:
            out.append(series[t] - series[t - lag])
    return out


def log_returns(prices: list[Optional[float]]) -> list[Optional[float]]:
    """Zwroty logarytmiczne; [0]=None (brak poprzednika)."""
    out: list[Optional[float]] = [None]
    for t in range(1, len(prices)):
        a, b = prices[t - 1], prices[t]
        if a is None or b is None or a <= 0 or b <= 0:
            out.append(None)
        else:
            out.append(math.log(b / a))
    return out


def realized_vol(prices: list[Optional[float]], window: int, annualize: int = 252) -> list[Optional[float]]:
    """Odchylenie std (próbkowe, ddof=1) log-zwrotów w oknie * sqrt(annualize).
    Wymaga `window` policzalnych zwrotów (czyli t>=window)."""
    lr = log_returns(prices)
    out: list[Optional[float]] = []
    for t in range(len(prices)):
        lo = t - window + 1
        if lo < 1:  # potrzeba `window` zwrotów, a lr[0] nie istnieje
            out.append(None)
            continue
        w = [lr[i] for i in range(lo, t + 1) if lr[i] is not None]
        if len(w) < window:
            out.append(None)
            continue
        m = sum(w) / len(w)
        var = sum((v - m) ** 2 for v in w) / (len(w) - 1)
        out.append(math.sqrt(var) * math.sqrt(annualize))
    return out


def linreg_slope(series: list[Optional[float]], window: int) -> list[Optional[float]]:
    """Nachylenie regresji liniowej ostatnich `window` punktów (x=0..window-1)."""
    out: list[Optional[float]] = []
    for t in range(len(series)):
        lo = t - window + 1
        if lo < 0:
            out.append(None)
            continue
        ys = series[lo:t + 1]
        if len(ys) < window or any(v is None for v in ys):
            out.append(None)
            continue
        n = len(ys)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        den = sum((xs[i] - mx) ** 2 for i in range(n))
        out.append(num / den if den else None)
    return out


def combine_half(a: list[Optional[float]], b: list[Optional[float]]) -> list[Optional[float]]:
    """Elementwise 0.5*a + 0.5*b; None gdy którakolwiek None."""
    out: list[Optional[float]] = []
    for x, y in zip(a, b):
        out.append(None if x is None or y is None else 0.5 * x + 0.5 * y)
    return out
