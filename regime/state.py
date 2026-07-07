"""Atomowy zapis regime_state.json (tmp + os.replace).

Zapis nigdy nie pozostawia częściowego pliku — czytelnicy (boty) widzą albo
poprzedni, albo nowy pełny stan. os.replace jest atomowy w obrębie systemu plików.
"""
from __future__ import annotations

import json
import os
import tempfile

from . import config


def write_state(payload: dict) -> None:
    """Zapisuje payload jako JSON do config.STATE_FILE atomowo (tmp w tym samym katalogu)."""
    target = config.STATE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".regime_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(target))  # atomowo
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
