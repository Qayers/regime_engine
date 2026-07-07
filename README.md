# regime_engine

Dzienny silnik oceny **reżimu rynku akcji USA** (risk-on / neutral / risk-off).
Silnik **nie handluje** — jego produktem jest `state/regime_state.json` oraz
statyczny dashboard HTML. Boty czytają stan w kolejnych etapach (poza zakresem).

> Status: **ETAP E1 (szkielet)** ukończony. Pełny runbook powstaje na E6.

## Środowisko (h55.seohost.pl / srv83775)

| Element | Wartość |
|---|---|
| Host | `h55.seohost.pl`, użytkownik `srv83775`, home `/home/srv83775` |
| Strefa serwera | CET/CEST (Europe) — cron 22:15 trafia ~15 min po zamknięciu US cały rok |
| Python (venv) | `/opt/alt/python313/bin/python3.13` (3.13.x, CloudLinux alt-python) |
| Uwaga `/tmp` | zamontowany `noexec` — venv i pliki wykonywalne wyłącznie pod `~` |
| Katalog aplikacji | `~/apps/regime_engine` |

## Instalacja od zera

```bash
cd ~/apps/regime_engine
/opt/alt/python313/bin/python3.13 -m venv venv
venv/bin/pip install -U pip
venv/bin/pip install -r requirements-dev.txt   # runtime + pytest
cp .env.example .env                            # następnie uzupełnij klucze API
venv/bin/python -c "from regime import db; db.init_db()"   # utworzenie schematu
venv/bin/python -m pytest -q                    # testy szkieletu
```

## Zmienne `.env`

Pełny opis w `.env.example`. Kluczowe: `FRED_API_KEY`, `FINNHUB_API_KEY`
(wymagane od E2), `EVENT_WATCHLIST`, `PERCENTILE_WINDOW`, `RISK_OFF_TH`/`RISK_ON_TH`,
`HYSTERESIS_SESSIONS`, `STALE_HOURS`, `PUBLIC_DIR`, `HTPASSWD_PATH`.

## Cron (przygotowane — instalacja ręczna, NIE instalowane automatycznie)

```cron
15 22 * * 1-5 cd ~/apps/regime_engine && venv/bin/python -m regime.run_daily >> logs/cron.log 2>&1
```

Serwer chodzi w CET/CEST, więc 22:15 = ~15 min po zamknięciu sesji US (zarówno
lato jak i zima, bo ET i CET przesuwają DST niemal równolegle). Bez korekty godziny.

## Struktura

```
regime_engine/
├── regime/           # pakiet: config, db, fetch_*, indicators, engine, state,
│                     #         dashboard, backfill, run_daily
├── state/            # regime_state.json + .run.lock
├── public/           # statyczny dashboard (E5)
├── logs/             # engine.log (Rotating 1 MB × 5) + cron.log
├── tests/            # testy jednostkowe (bez sieci)
├── data.sqlite3      # baza (tworzona przez db.init_db())
├── requirements.txt  # runtime: requests, python-dotenv
└── .env / .env.example
```

## TODO (kolejne etapy / v2)

- E2: fetchery Stooq/FRED/Finnhub. E3: backfill. E4: engine+state. E5: dashboard. E6: run_daily+runbook.
- v2 (poza zakresem teraz): short interest FINRA, put/call CBOE, IG OAS (`BAMLC0A0CM`) i 10Y (`DGS10`) do score.
- Procedura „co sprawdzić przy stale_safe" — zostanie opisana na E6.
