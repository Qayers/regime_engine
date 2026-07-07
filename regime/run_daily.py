"""Główny entrypoint crona: fetch → compute → zapis historii → stan → dashboard. — ETAP E6.

Lockfile (state/.run.lock, martwy >30 min = przejmij). Log podsumowania jedną linią.
Uruchomienie: `venv/bin/python -m regime.run_daily`.
"""
from __future__ import annotations

# TODO(E6): orkiestracja pełnego przebiegu + lockfile + podsumowanie.


def main() -> int:
    """Placeholder E1 — pełna orkiestracja w E6."""
    raise NotImplementedError("run_daily zostanie zaimplementowany w ETAPIE E6")


if __name__ == "__main__":
    raise SystemExit(main())
