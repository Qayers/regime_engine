"""Konfiguracja silnika: wczytanie .env, ścieżki, stałe, logging.

Jedyne miejsce z twardymi ścieżkami. Reszta modułów importuje stąd.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Ograniczenie wątków bibliotek natywnych — konto współdzielone ma limity CPU/procesów.
# Ustawiane przed ewentualnym importem numpy/pandas gdziekolwiek w procesie.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv może brakować w minimalnym środowisku testowym
    load_dotenv = None

# --- ścieżki bazowe -----------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # ~/apps/regime_engine
ENV_PATH = BASE_DIR / ".env"
if load_dotenv is not None and ENV_PATH.exists():
    load_dotenv(ENV_PATH)

DATA_DB = BASE_DIR / "data.sqlite3"
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "regime_state.json"
LOCK_FILE = STATE_DIR / ".run.lock"
LOGS_DIR = BASE_DIR / "logs"
LOG_FILE = LOGS_DIR / "engine.log"
# PUBLIC_DIR bywa docrootem subdomeny — NIE tworzymy go tutaj (robi to dashboard.py na E5).
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR") or (BASE_DIR / "public")).expanduser()

# Katalogi zawsze-lokalne (stan + logi) tworzymy od razu, by moduły mogły pisać.
for _d in (STATE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- wersje -------------------------------------------------------------------
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1

# --- klucze API (mogą być puste do E2) ----------------------------------------
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "").strip()  # ceny EOD (zastąpiło Stooq)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

# --- symbole cen (Tiingo — czyste tickery, bez sufiksu; Stooq zablokowany 2026-07-07) ---
# CORE wchodzą do score — strukturalne, świadomie NIE w .env (zmiana = rewalidacja).
CORE_SYMBOLS = ["spy", "rsp", "qqq", "iwm"]
# WATCH: tylko pobieranie/zapis (przyszły screener), awaria/brak symbolu = pominięcie.
WATCH_SYMBOLS = ["xle", "smh", "igv", "orcl", "mu"]
ALL_PRICE_SYMBOLS = CORE_SYMBOLS + WATCH_SYMBOLS

# --- serie FRED ---------------------------------------------------------------
FRED_SERIES_SCORE = ["BAMLH0A0HYM2", "VIXCLS"]   # HY OAS + VIX — wchodzą do score v1
FRED_SERIES_STORE = ["DGS10", "BAMLC0A0CM"]       # 10Y + IG OAS — tylko zapis (rezerwa v2)
FRED_SERIES_ALL = FRED_SERIES_SCORE + FRED_SERIES_STORE

# --- kalendarz zdarzeń --------------------------------------------------------
EVENT_WATCHLIST = [
    s.strip().upper()
    for s in os.environ.get(
        "EVENT_WATCHLIST", "MSFT,GOOGL,AMZN,META,NVDA,AVGO,ORCL,CRWV,MU,SNDK"
    ).split(",")
    if s.strip()
]

# --- parametry silnika --------------------------------------------------------
PERCENTILE_WINDOW = int(os.environ.get("PERCENTILE_WINDOW", "250"))
RISK_OFF_TH = float(os.environ.get("RISK_OFF_TH", "65"))
RISK_ON_TH = float(os.environ.get("RISK_ON_TH", "35"))
HYSTERESIS_SESSIONS = int(os.environ.get("HYSTERESIS_SESSIONS", "2"))
STALE_HOURS = float(os.environ.get("STALE_HOURS", "48"))

# Wagi komponentów score (suma = 1.0). Score = 100 * Σ waga_i * komponent_i.
WEIGHTS = {"breadth": 0.30, "credit": 0.30, "vol": 0.25, "rotation": 0.15}

# --- sieć ---------------------------------------------------------------------
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "15"))
HTTP_RETRIES = 3
HTTP_BACKOFF_S = [2, 4, 8]

# --- dashboard / auth ---------------------------------------------------------
HTPASSWD_PATH = os.environ.get("HTPASSWD_PATH", "").strip()

# --- logging ------------------------------------------------------------------
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP = 5
_logging_configured = False


def setup_logging() -> None:
    """Konfiguruje logger 'regime' raz (RotatingFileHandler 1 MB × 5 + konsola)."""
    global _logging_configured
    if _logging_configured:
        return
    root = logging.getLogger("regime")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)
    root.propagate = False
    _logging_configured = True


def get_logger(name: str = "regime") -> logging.Logger:
    """Zwraca logger podpięty pod skonfigurowany root 'regime'."""
    setup_logging()
    if name == "regime":
        return logging.getLogger("regime")
    return logging.getLogger(f"regime.{name}")
