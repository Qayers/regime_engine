"""Atomowy zapis regime_state.json (tmp + os.replace). — ETAP E4.

Zapis nigdy nie pozostawia częściowego pliku — czytelnicy (boty) widzą albo
poprzedni, albo nowy pełny stan.
"""
from __future__ import annotations

# TODO(E4): write_state(payload) — zapis atomowy do config.STATE_FILE.
