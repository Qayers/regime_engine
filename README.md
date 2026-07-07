# regime_engine

Dzienny silnik oceny **reżimu rynku akcji USA** (risk-on / neutral / risk-off, `score` 0–100).
Silnik **nie handluje** — jego jedynym produktem jest `state/regime_state.json` oraz statyczny
dashboard HTML. Konsumenci (boty) czytają **tylko plik stanu** — zero sprzężenia sieciowego.

> Status: **E1–E6 ukończone.** Dashboard live: https://regime.uluru.space/

## Co robi / czego NIE robi

- ✅ Codziennie po zamknięciu US liczy score reżimu z 4 komponentów i publikuje stan + dashboard.
- ✅ Trzyma pełną historię reżimu (`regime_history`) do wykresu i analiz.
- ❌ Nie składa zleceń, nie zna pozycji, nie łączy się z brokerami ani z botami.
- ❌ Nie jest detektorem „pęknięcia bańki AI" — patrz *Właściwość konstrukcji* niżej.

## Architektura

```
Tiingo (EOD) ─┐
FRED (makro) ─┼─► SQLite (data.sqlite3) ─► engine ─► state/regime_state.json ─► [boty czytają plik]
Finnhub (kal.)┘                              │
                                             └─► dashboard ─► public/{index.html,history.json,.htaccess}
```

Wszystko jako **procesy krótkotrwałe z crona** — shared hosting (CloudLinux) reapuje długo żyjące
procesy, więc nie ma demonów/serwisów. Kolejność w `run_daily`: lock → fetch → engine → dashboard.

## Score i komponenty

`score = 0.30·breadth + 0.30·credit + 0.25·vol + 0.15·rotation` (komponenty 0–100, **100 = maks. stres**).

| komponent | waga | definicja |
|---|---|---|
| **breadth** (szerokość) | 0.30 | percentyl(−mom20 z RSP/SPY) — zwężanie rynku (equal-weight słabnie względem cap-weight) podnosi stres |
| **credit** (kredyt) | 0.30 | ½·percentyl(HY OAS) + ½·percentyl(zmiana 20d HY OAS) |
| **vol** (zmienność) | 0.25 | ½·percentyl(VIX) + ½·percentyl(rv5 z QQQ) |
| **rotation** (rotacja) | 0.15 | percentyl(nachylenie 20d z ln(IWM)−ln(QQQ)) — trwała ucieczka od megacapów podnosi stres |

Percentyle liczone w oknie kroczącym `PERCENTILE_WINDOW` (250 sesji); przy krótszej historii
`warmup=true`. Makro dołączane **as-of** (ostatnia wartość ≤ data sesji — FRED publikuje z lagiem).

### ⚠ Właściwość konstrukcji — REVISIT wag po sezonie Q2

Silnik jest **detektorem stresu SYSTEMOWEGO**, nie pęknięcia wąskiej grupy (np. megacapów AI).
W wyprzedaży skoncentrowanej na megacapach RSP/SPY **rośnie** (equal-weight spada płycej), więc
`breadth` czyta spokój i **tłumi** score — działa jako przeciwwaga. Komponent najlepiej łapiący
taki scenariusz (`rotation`) ma najmniejszą wagę 0.15. Przykład (czerwiec 2026): vol 67→86,
credit 20→55, rotation 75→96, a score ledwie ~48.

**Do rozstrzygnięcia po sezonie Q2** (na danych z **≥2–3 epizodów**, nie z jednego — zakaz
dostrajania pod znany wynik): czy `rotation` nie powinno ważyć więcej kosztem `breadth`.

## Tryby, progi, histereza

- `risk_on` gdy score ≤ `RISK_ON_TH` (35); `risk_off` gdy score ≥ `RISK_OFF_TH` (65); pomiędzy `neutral`.
- **Histereza**: zmiana trybu wymaga `HYSTERESIS_SESSIONS` (2) kolejnych sesji w nowej strefie (anty-flapping).
- **`mode_since` = sesja POTWIERDZENIA** (druga z histerezy), **nie** pierwsza sesja w nowej strefie —
  konsument stanu nie powinien czytać tej daty jako „pierwszego dnia strefy".
- **Mapowanie na strategie** (bez zmian): w `neutral` np. `short_pullback` działa **połową wielkości** —
  score ~48 to sytuacja **grywalna**, nie bezczynność.

## stale_safe (świeżość)

Jeśli którekolwiek **źródło wchodzące do score** jest przeterminowane, tryb jest wymuszany na
`stale_safe` (niezależnie od score), z listą źródeł w `stale_sources`.

- Lag liczony w **DNIACH ROBOCZYCH** (nie kalendarzowych — inaczej fałszywy stale w poniedziałki,
  bo FRED publikuje z lagiem ~1 dnia roboczego → piątkowa wartość w poniedziałek to 72 h kalendarzowe).
- Progi (`.env`): ceny CORE `STALE_CORE_MAX_BDAYS` (2), makro FRED `STALE_FRED_MAX_BDAYS` (3).
- Liczone z **`MAX(date)` per symbol CORE + serię FRED_SCORE bezpośrednio z tabel** — **nie** z
  `source_health` (tam sukces jednego watchowego symbolu maskowałby padnięcie CORE). `source_health`
  służy wyłącznie jako diagnostyka na dashboard.

## Dane / źródła

| źródło | do czego | uwagi |
|---|---|---|
| **Tiingo** EOD | ceny CORE (spy/rsp/qqq/iwm) + watch (xle/smh/igv/orcl/mu) | klucz `TIINGO_API_KEY`; darmowy ~50 req/h — dzienny run ~9 req; pełna historia 1 zapytaniem/symbol |
| **FRED** | HY OAS + VIX (score); DGS10 + IG OAS (store) | klucz `FRED_API_KEY` (reużyty z trade_bot) |
| **Finnhub** | earnings + IPO (tylko kalendarz na dashboard) | klucz `FINNHUB_API_KEY` |

HTTP 429 (limit) przerywa retry i pętlę po symbolach **natychmiast** (nie spala limitu), status
`rate_limited` odrębny od `error`. *(Stooq porzucony — anti-bot: JS proof-of-work + blokada IP.)*

## Instalacja od zera

```bash
cd ~/apps/regime_engine
/opt/alt/python313/bin/python3.13 -m venv venv          # /tmp jest noexec → venv pod ~
venv/bin/pip install -U pip
venv/bin/pip install -r requirements-dev.txt            # runtime + pytest
cp .env.example .env                                    # uzupełnij klucze API i PUBLIC_DIR
venv/bin/python -c "from regime import db; db.init_db()"
venv/bin/python -m regime.backfill --from 2024-01-02    # backfill historii (ceny + makro)
venv/bin/python -m regime.engine                        # policz regime_history + stan
venv/bin/python -m regime.dashboard                     # wygeneruj dashboard
venv/bin/python -m pytest -q                            # 12 testów
```

## Uruchomienie dzienne

```bash
venv/bin/python -m regime.run_daily      # lock → fetch → engine → dashboard; kod 0=OK, 1=twardy błąd
```

Fetchery są **miękkie** (błąd loguje się, bieg trwa — stale_safe zadziała), engine **twardy**.
Lock (`state/.run.lock`) chroni przed równoległymi biegami; osierocony >30 min jest przejmowany.

## Cron

```cron
20 0 * * 2-6 cd ~/apps/regime_engine && venv/bin/python -m regime.run_daily >> logs/cron.log 2>&1
```

- **00:20 Europe/Warsaw, wt–sob** (obsługuje sesje US pon–pt: sesja z wieczora D jest gotowa u dostawcy
  po północy D+1). Empiria (2026-07-07): pełny komplet CORE w Tiingo pojawił się **~00:06 CEST**, a
  o 23:06 rsp/iwm jeszcze nie było — dlatego run **po północy**, nie wieczorem. Reguła świeżości i tak
  chroni przed danymi D-1 (nie wywali stanu w stale_safe).
- Serwer chodzi w Europe/Warsaw; offset do US ET jest stały (~6 h) cały rok.

**Instalacja crona — BEZPIECZNIE** (crontab jest współdzielony z botami tradingowymi — **nigdy**
`crontab -l | grep -v | crontab -`):

```bash
crontab -l > ~/crontab.bak.$(date +%Y%m%d_%H%M%S)   # 1. backup
crontab -l > ~/ct.tmp                                # 2. edytuj kopię (dopisz linię wyżej)
crontab ~/ct.tmp                                     # 3. wgraj z pliku
crontab -l | grep regime && diff <(crontab -l) ~/crontab.bak.*  # 4. verify + diff
```

## Wyjścia

**`state/regime_state.json`** (zapisywany atomowo — tmp + `os.replace`):

```json
{
  "schema_version": 1, "engine_version": "1.0.0", "generated_at_utc": "…Z",
  "session_date": "2026-07-07", "score": 39.81, "mode": "neutral",
  "mode_since": "2026-04-07",
  "components": {"breadth": …, "credit": …, "vol": …, "rotation": …},
  "freshness": {"tiingo": "ok", "fred": "ok", "finnhub": "ok"},
  "stale_sources": [], "next_events_7d": [{"kind": "ipo", "symbol": "TARX", "date": "…"}]
}
```

**`public/`** — statyczny dashboard (index.html + history.json + .htaccess), **zero CDN** (wykres to
inline SVG). Serwowany z docroota subdomeny (`PUBLIC_DIR`). `.htaccess`: `noindex` + `Options -Indexes`
+ opcjonalny basic-auth (gdy `HTPASSWD_PATH` ustawiony i plik istnieje). Live: https://regime.uluru.space/
*(obecnie bez auth — dane niewrażliwe, noindex włączony).*

## Ograniczenia hostingu

Brak roota, brak konfiguracji nginx/Apache, brak demonów/serwisów. CloudLinux reapuje długo żyjące
procesy → **tylko cron** (procesy krótkotrwałe). `/tmp` jest `noexec` → venv i pliki wykonywalne pod `~`.

## Struktura repo

```
regime_engine/
├── regime/           config, db, fetch_{tiingo,fred,finnhub}, indicators, engine, state, dashboard, backfill, run_daily
├── tests/            test_engine.py (12 testów, bez sieci)
├── requirements.txt  runtime: requests, python-dotenv
└── .env.example      (sekrety w .env — poza gitem)
```
*(pomijane przez git: `.env`, `venv/`, `data.sqlite3`, `state/`, `logs/`, `public/`)*

## TODO / v2 (poza zakresem)

- Rozstrzygnąć wagi po Q2 (patrz *Właściwość konstrukcji*).
- v2 do score: short interest FINRA, put/call CBOE, IG OAS (`BAMLC0A0CM`), 10Y (`DGS10`).
