"""Generator statycznego dashboardu (index.html + history.json + .htaccess). — ETAP E5.

Bez frameworków i procesu serwera — cron generuje pliki statyczne do PUBLIC_DIR, które
serwuje Apache/nginx. ZERO zależności zewnętrznych (żadnego CDN): wykres to inline SVG
budowany vanilla-JS z history.json (reliability > simplicity — dashboard nie może paść,
gdy padnie CDN). Meta robots noindex + .htaccess (Options -Indexes, X-Robots-Tag, opcjonalny
basic-auth gdy HTPASSWD_PATH ustawiony i plik istnieje).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from . import config, db

log = config.get_logger("dashboard")


def _load_series() -> list[dict]:
    """Cała historia z regime_history do wykresu (posortowana rosnąco)."""
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT date, score, mode, comp_breadth, comp_credit, comp_vol, comp_rotation "
            "FROM regime_history ORDER BY date"
        ).fetchall()
    return [
        {"date": r["date"], "score": r["score"], "mode": r["mode"],
         "breadth": r["comp_breadth"], "credit": r["comp_credit"],
         "vol": r["comp_vol"], "rotation": r["comp_rotation"]}
        for r in rows
    ]


def _load_state() -> dict:
    """Bieżący stan z regime_state.json (albo pusty gdy brak)."""
    try:
        with open(config.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_source_health() -> list[dict]:
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT source, last_success_utc, last_row_date, status FROM source_health ORDER BY source"
        ).fetchall()
    return [dict(r) for r in rows]


def _atomic_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dash.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
        os.chmod(str(path), 0o644)  # mkstemp daje 600 → Apache nie odczyta (403); pliki statyczne 644
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _htaccess_text() -> str:
    lines = [
        "# Wygenerowane przez regime_engine dashboard.py — NIE edytować ręcznie.",
        "Options -Indexes",
        "<IfModule mod_headers.c>",
        '  Header set X-Robots-Tag "noindex, nofollow"',
        "</IfModule>",
    ]
    htp = config.HTPASSWD_PATH
    if htp and os.path.exists(htp):
        lines += [
            "",
            "AuthType Basic",
            'AuthName "Regime Engine"',
            f"AuthUserFile {htp}",
            "Require valid-user",
        ]
    else:
        lines += ["", "# basic-auth wyłączony: HTPASSWD_PATH pusty lub plik nie istnieje"]
    return "\n".join(lines) + "\n"


def render_dashboard() -> dict:
    """Generuje PUBLIC_DIR/{history.json,index.html,.htaccess}. Zwraca podsumowanie."""
    series = _load_series()
    state = _load_state()
    health = _load_source_health()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": state,
        "source_health": health,
        "series": series,
    }
    pub = config.PUBLIC_DIR
    _atomic_write(pub / "history.json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    _atomic_write(pub / "index.html", _INDEX_HTML)
    _atomic_write(pub / ".htaccess", _htaccess_text())
    log.info("DASHBOARD: %d punktów historii → %s (auth=%s)",
             len(series), pub, "on" if (config.HTPASSWD_PATH and os.path.exists(config.HTPASSWD_PATH)) else "off")
    return {"points": len(series), "public_dir": str(pub), "has_state": bool(state)}


# --- Statyczny szablon (bez CDN; dane z history.json przez fetch) --------------

_INDEX_HTML = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Regime Engine</title>
<style>
  :root{
    --bg:#0e1116; --card:#171b22; --line:#242a33; --txt:#e6e9ef; --muted:#8b94a3;
    --on:#26c281; --neutral:#e0a740; --off:#e5484d; --stale:#8b5cf6; --accent:#4c8dff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{display:flex;align-items:baseline;gap:12px;padding:18px 22px;border-bottom:1px solid var(--line)}
  header h1{font-size:17px;margin:0;font-weight:600;letter-spacing:.3px}
  header .ver{color:var(--muted);font-size:12px}
  main{max-width:1000px;margin:0 auto;padding:20px;display:grid;gap:16px;
    grid-template-columns:repeat(2,1fr)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .card.wide{grid-column:1/-1}
  .card h2{margin:0 0 12px;font-size:12px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}
  .score-wrap{display:flex;align-items:center;gap:20px}
  .score{font-size:56px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
  .badge{display:inline-block;padding:5px 12px;border-radius:999px;font-weight:600;font-size:13px}
  .meta{color:var(--muted);font-size:13px;margin-top:6px}
  .meta b{color:var(--txt);font-weight:600}
  .warn{margin-top:10px;padding:8px 12px;border-radius:8px;background:rgba(229,72,77,.12);
    border:1px solid rgba(229,72,77,.4);color:#ff9a9d;font-size:13px}
  .bars{display:flex;flex-direction:column;gap:10px}
  .bar-row{display:grid;grid-template-columns:80px 1fr 44px;align-items:center;gap:10px}
  .bar-row .lbl{color:var(--muted);font-size:13px}
  .track{height:10px;background:#0c0f14;border-radius:6px;overflow:hidden}
  .fill{height:100%;border-radius:6px}
  .bar-row .val{text-align:right;font-variant-numeric:tabular-nums;font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
  .ev{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)}
  .ev:last-child{border-bottom:0}
  .ev .k{color:var(--muted);font-size:12px}
  svg{width:100%;height:auto;display:block}
  .chart-tip{fill:var(--txt);font-size:11px}
  footer{max-width:1000px;margin:0 auto;padding:8px 20px 30px;color:var(--muted);font-size:12px}
  @media(max-width:720px){main{grid-template-columns:1fr}.score{font-size:44px}}
</style>
</head>
<body>
<header><h1>Regime Engine</h1><span class="ver" id="ver"></span></header>
<main>
  <section class="card" id="c-state">
    <h2>Stan bieżący</h2>
    <div class="score-wrap">
      <div class="score" id="score">–</div>
      <div>
        <span class="badge" id="mode">–</span>
        <div class="meta">sesja: <b id="sess">–</b></div>
        <div class="meta" id="since-wrap">tryb od potwierdzenia: <b id="since">–</b></div>
      </div>
    </div>
    <div id="stale"></div>
  </section>

  <section class="card">
    <h2>Komponenty (0–100, 100 = stres)</h2>
    <div class="bars" id="bars"></div>
  </section>

  <section class="card wide">
    <h2>Historia score (progi 35 / 65)</h2>
    <div id="chart"></div>
  </section>

  <section class="card">
    <h2>Świeżość i źródła</h2>
    <table id="health"><thead><tr><th>źródło</th><th>status</th><th>ost. wiersz</th><th>ost. sukces (UTC)</th></tr></thead><tbody></tbody></table>
  </section>

  <section class="card">
    <h2>Wydarzenia (7 dni)</h2>
    <div id="events"><div class="meta">–</div></div>
  </section>
</main>
<footer id="foot">Ładowanie…</footer>

<script>
const MODE = {
  risk_on:  {c:'--on',      t:'RISK-ON'},
  neutral:  {c:'--neutral', t:'NEUTRAL'},
  risk_off: {c:'--off',     t:'RISK-OFF'},
  stale_safe:{c:'--stale',  t:'STALE-SAFE'},
};
const cssv = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const esc = s => String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));

function scoreColor(v){ // 0=on(zielony) .. 100=off(czerwony)
  if(v==null) return cssv('--muted');
  if(v>=65) return cssv('--off');
  if(v<=35) return cssv('--on');
  return cssv('--neutral');
}

function renderState(st){
  document.getElementById('ver').textContent = st.engine_version ? 'v'+st.engine_version : '';
  const m = MODE[st.mode] || {c:'--muted',t:(st.mode||'–').toUpperCase()};
  const sc = st.score==null?'–':Number(st.score).toFixed(1);
  const el = document.getElementById('score');
  el.textContent = sc; el.style.color = scoreColor(st.score);
  const b = document.getElementById('mode');
  b.textContent = m.t; b.style.background = 'color-mix(in srgb,'+cssv(m.c)+' 22%, transparent)';
  b.style.color = cssv(m.c); b.style.border='1px solid '+cssv(m.c);
  document.getElementById('sess').textContent = st.session_date || '–';
  document.getElementById('since').textContent = st.mode_since || '–';
  const sd = document.getElementById('stale');
  if(st.stale_sources && st.stale_sources.length){
    sd.className='warn';
    sd.textContent='⚠ stale_safe — przeterminowane źródła: '+st.stale_sources.join(', ');
  } else { sd.className=''; sd.textContent=''; }
}

function renderBars(comp){
  const order=[['breadth','szerokość'],['credit','kredyt'],['vol','zmienność'],['rotation','rotacja']];
  const host=document.getElementById('bars'); host.innerHTML='';
  order.forEach(([k,lbl])=>{
    const v=comp&&comp[k]!=null?comp[k]:null;
    const row=document.createElement('div'); row.className='bar-row';
    row.innerHTML='<span class="lbl">'+lbl+'</span>'+
      '<span class="track"><span class="fill" style="width:'+(v==null?0:v)+'%;background:'+scoreColor(v)+'"></span></span>'+
      '<span class="val">'+(v==null?'–':v.toFixed(1))+'</span>';
    host.appendChild(row);
  });
}

function renderHealth(rows){
  const tb=document.querySelector('#health tbody'); tb.innerHTML='';
  (rows||[]).forEach(r=>{
    const ok = r.status==='ok';
    const col = ok?cssv('--on'):(r.status==='rate_limited'?cssv('--neutral'):cssv('--off'));
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+esc(r.source)+'</td>'+
      '<td><span class="dot" style="background:'+col+'"></span>'+esc(r.status)+'</td>'+
      '<td>'+esc(r.last_row_date)+'</td>'+'<td>'+esc(r.last_success_utc)+'</td>';
    tb.appendChild(tr);
  });
}

function renderEvents(evs){
  const host=document.getElementById('events');
  if(!evs||!evs.length){ host.innerHTML='<div class="meta">brak w oknie 7 dni</div>'; return; }
  host.innerHTML='';
  evs.forEach(e=>{
    const d=document.createElement('div'); d.className='ev';
    d.innerHTML='<span>'+esc(e.symbol)+' <span class="k">'+esc(e.kind)+'</span></span><span class="k">'+esc(e.date)+'</span>';
    host.appendChild(d);
  });
}

function renderChart(series){
  const W=940,H=280,PL=34,PR=12,PT=12,PB=24, iw=W-PL-PR, ih=H-PT-PB;
  const n=series.length; if(!n){document.getElementById('chart').textContent='brak danych';return;}
  const x=i=> PL + (n<=1?0:i/(n-1)*iw);
  const y=v=> PT + (1-v/100)*ih;
  const NS='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(NS,'svg'); svg.setAttribute('viewBox','0 0 '+W+' '+H);
  const add=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);svg.appendChild(e);return e;};
  // strefy tła
  add('rect',{x:PL,y:PT,width:iw,height:y(65)-PT,fill:cssv('--off'),opacity:0.06});
  add('rect',{x:PL,y:y(35),width:iw,height:PT+ih-y(35),fill:cssv('--on'),opacity:0.06});
  // linie pomocnicze 0/35/50/65/100
  [0,35,50,65,100].forEach(g=>{
    add('line',{x1:PL,y1:y(g),x2:W-PR,y2:y(g),stroke:cssv('--line'),'stroke-width':1,
      'stroke-dasharray':(g===35||g===65)?'4 3':''});
    add('text',{x:4,y:y(g)+3,fill:cssv('--muted'),'font-size':10}).textContent=g;
  });
  // polyline score
  let d=''; series.forEach((p,i)=>{ if(p.score==null)return; d+=(d?'L':'M')+x(i).toFixed(1)+' '+y(p.score).toFixed(1)+' '; });
  add('path',{d:d,fill:'none',stroke:cssv('--accent'),'stroke-width':1.5});
  // ostatni punkt
  const last=series[n-1]; add('circle',{cx:x(n-1),cy:y(last.score),r:3,fill:scoreColor(last.score)});
  // osie X — ~6 etykiet YYYY-MM
  const ticks=Math.min(6,n);
  for(let t=0;t<ticks;t++){ const i=Math.round(t/(Math.max(1,ticks-1))*(n-1));
    add('text',{x:x(i),y:H-6,fill:cssv('--muted'),'font-size':10,'text-anchor':'middle'})
      .textContent=(series[i].date||'').slice(0,7); }
  // hover
  const tip=add('text',{class:'chart-tip','text-anchor':'middle'}); tip.style.display='none';
  const vline=add('line',{stroke:cssv('--muted'),'stroke-width':1,opacity:.5}); vline.style.display='none';
  const hit=add('rect',{x:PL,y:PT,width:iw,height:ih,fill:'transparent'});
  hit.addEventListener('mousemove',ev=>{
    const r=svg.getBoundingClientRect(); const px=(ev.clientX-r.left)/r.width*W;
    let i=Math.round((px-PL)/iw*(n-1)); i=Math.max(0,Math.min(n-1,i)); const p=series[i];
    if(p.score==null)return;
    vline.setAttribute('x1',x(i));vline.setAttribute('x2',x(i));vline.setAttribute('y1',PT);vline.setAttribute('y2',PT+ih);
    vline.style.display=''; tip.setAttribute('x',Math.max(PL+40,Math.min(W-PR-40,x(i))));
    tip.setAttribute('y',PT+12); tip.textContent=p.date+'  score '+p.score.toFixed(1)+'  '+(p.mode||'');
    tip.style.display='';
  });
  hit.addEventListener('mouseleave',()=>{vline.style.display='none';tip.style.display='none';});
  const host=document.getElementById('chart'); host.innerHTML=''; host.appendChild(svg);
}

fetch('history.json',{cache:'no-store'}).then(r=>r.json()).then(d=>{
  const st=d.state||{};
  renderState(st);
  renderBars(st.components);
  renderChart(d.series||[]);
  renderHealth(d.source_health);
  renderEvents(st.next_events_7d);
  document.getElementById('foot').textContent =
    'wygenerowano '+(d.generated_at_utc||'?')+' · '+((d.series||[]).length)+' sesji w historii';
}).catch(e=>{
  document.getElementById('foot').textContent='Błąd ładowania history.json: '+e;
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    r = render_dashboard()
    print(f"dashboard: {r['points']} punktów → {r['public_dir']} (state={'tak' if r['has_state'] else 'NIE'})")
