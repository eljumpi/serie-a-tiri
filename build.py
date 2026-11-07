#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Classifiche alternative — punti assegnati per TIRI IN PORTA.

Regola: in ogni partita la squadra con piu' tiri in porta "vince" (3 punti).
Tiri in porta pari = pareggio (1 punto a testa).

Fonte dati: API pubbliche ESPN (nessuna API key).
  - elenco partite:  site.api.espn.com     /apis/site/v2/sports/soccer/<lega>/scoreboard?dates=YYYYMMDD
  - statistiche:     site.web.api.espn.com /apis/site/v2/sports/soccer/<lega>/summary?event=<id>

Uso:
    python3 build.py                 # aggiornamento incrementale + genera index.html
    python3 build.py --full          # riscarica tutto da inizio stagione
    python3 build.py --offline       # ricostruisce solo dalla cache locale, senza rete
    python3 build.py --league eng.1  # altro campionato (Premier League)

Solo libreria standard Python 3.
"""

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
STATS_DIR = os.path.join(RAW_DIR, "stats")
PROC_DIR = os.path.join(DATA_DIR, "processed")
OUT_HTML = os.path.join(BASE_DIR, "index.html")

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"
SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event}"

LEAGUE_LABELS = {
    "ita.1": "Serie A",
    "eng.1": "Premier League",
    "esp.1": "LaLiga",
    "ger.1": "Bundesliga",
    "fra.1": "Ligue 1",
}

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) classifiche-alternative/1.0"
REQUEST_PAUSE = 0.35          # secondi fra una richiesta e l'altra
LOOKBACK_REFRESH_DAYS = 10    # giorni recenti sempre riscaricati


# ----------------------------------------------------------------------------
# UTILITY
# ----------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def ensure_dirs():
    for d in (RAW_DIR, STATS_DIR, PROC_DIR):
        os.makedirs(d, exist_ok=True)


def http_json(url, retries=3, timeout=25):
    """GET -> dict. Ritorna None su errore definitivo."""
    ctx = ssl.create_default_context()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (400, 404):
                return None
        except Exception as e:                      # noqa: BLE001
            last = str(e)
        time.sleep(1.2 * (attempt + 1))
    log("    ! richiesta fallita (%s): %s" % (last, url))
    return None


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                               # noqa: BLE001
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def parse_iso(s):
    """'2026-11-01T19:45Z' -> datetime UTC-naive."""
    if not s:
        return None
    s = s.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def to_rome(d):
    """UTC naive -> ora italiana approssimata (CET/CEST senza tzdata)."""
    if d is None:
        return None
    year = d.year
    # ultima domenica di marzo / ottobre
    mar = dt.datetime(year, 3, 31)
    mar -= dt.timedelta(days=(mar.weekday() + 1) % 7)
    oct_ = dt.datetime(year, 10, 31)
    oct_ -= dt.timedelta(days=(oct_.weekday() + 1) % 7)
    offset = 2 if mar <= d < oct_ else 1
    return d + dt.timedelta(hours=offset)


def season_bounds(today=None):
    """Stagione europea: agosto -> giugno."""
    today = today or dt.date.today()
    start_year = today.year if today.month >= 7 else today.year - 1
    return dt.date(start_year, 7, 15), dt.date(start_year + 1, 6, 30)


def daterange(d0, d1):
    cur = d0
    while cur <= d1:
        yield cur
        cur += dt.timedelta(days=1)


# ----------------------------------------------------------------------------
# DOWNLOAD
# ----------------------------------------------------------------------------

def fetch_events(league, full=False, offline=False):
    """Scarica/aggiorna l'indice partite. Ritorna dict event_id -> evento."""
    index_path = os.path.join(RAW_DIR, "events_%s.json" % league.replace(".", "_"))
    store = load_json(index_path, default={"events": {}, "days": {}}) or {"events": {}, "days": {}}
    store.setdefault("events", {})
    store.setdefault("days", {})

    if offline:
        log("  modalita' offline: uso la cache (%d partite)" % len(store["events"]))
        return store["events"], index_path, store

    s_start, s_end = season_bounds()
    today = dt.date.today()
    horizon = min(s_end, today + dt.timedelta(days=21))
    refresh_from = today - dt.timedelta(days=LOOKBACK_REFRESH_DAYS)

    days = list(daterange(s_start, horizon))
    to_fetch = []
    for d in days:
        key = d.strftime("%Y%m%d")
        cached = store["days"].get(key)
        if full or cached is None or d >= refresh_from:
            to_fetch.append(d)

    log("  giorni da interrogare: %d (su %d della stagione)" % (len(to_fetch), len(days)))

    for i, d in enumerate(to_fetch, 1):
        key = d.strftime("%Y%m%d")
        data = http_json(SCOREBOARD.format(league=league, date=key))
        time.sleep(REQUEST_PAUSE)
        if data is None:
            continue
        evs = data.get("events") or []
        store["days"][key] = len(evs)
        for ev in evs:
            parsed = parse_event(ev)
            if parsed:
                store["events"][parsed["id"]] = parsed
        if i % 25 == 0 or i == len(to_fetch):
            log("    %d/%d giorni  -  partite note: %d" % (i, len(to_fetch), len(store["events"])))

    save_json(index_path, store)
    return store["events"], index_path, store


def parse_event(ev):
    try:
        comps = (ev.get("competitions") or [{}])[0]
        competitors = comps.get("competitors") or []
        if len(competitors) != 2:
            return None
        home = away = None
        for c in competitors:
            team = c.get("team") or {}
            side = {
                "id": str(team.get("id") or ""),
                "name": team.get("displayName") or team.get("name") or "?",
                "short": team.get("shortDisplayName") or team.get("abbreviation") or "?",
                "abbr": team.get("abbreviation") or "",
                "score": c.get("score"),
            }
            if c.get("homeAway") == "home":
                home = side
            else:
                away = side
        if not home or not away:
            return None
        status = ((ev.get("status") or {}).get("type") or {})
        return {
            "id": str(ev.get("id")),
            "date": ev.get("date"),
            "state": status.get("state") or "pre",       # pre | in | post
            "completed": bool(status.get("completed")),
            "home": home,
            "away": away,
        }
    except Exception:                                    # noqa: BLE001
        return None


def fetch_stats(league, events, full=False, offline=False):
    """Scarica i tiri in porta per le partite concluse. Ritorna dict event_id -> {home,away}."""
    cache_path = os.path.join(RAW_DIR, "shots_%s.json" % league.replace(".", "_"))
    cache = load_json(cache_path, default={}) or {}

    finished = [e for e in events.values() if e["completed"]]
    if offline:
        log("  modalita' offline: %d partite con tiri in cache" % len(cache))
        return cache, cache_path

    # Una partita conclusa puo' risultare senza statistiche se ESPN non le ha
    # ancora pubblicate: in quel caso NON la si considera acquisita, si riprova
    # a ogni esecuzione finche' il dato non arriva.
    def acquired(entry):
        return bool(entry) and entry.get("home") is not None and entry.get("away") is not None

    missing = [e for e in finished if full or not acquired(cache.get(e["id"]))]
    retry = sum(1 for e in missing if e["id"] in cache)
    log("  partite concluse: %d  -  da scaricare: %d (di cui %d gia' tentate senza esito)"
        % (len(finished), len(missing), retry))

    for i, ev in enumerate(sorted(missing, key=lambda x: x["date"] or ""), 1):
        data = http_json(SUMMARY.format(league=league, event=ev["id"]))
        time.sleep(REQUEST_PAUSE)
        shots = extract_shots(data, ev) if data else None
        if shots:
            cache[ev["id"]] = shots
        else:
            cache[ev["id"]] = {"home": None, "away": None}
        if i % 20 == 0 or i == len(missing):
            log("    %d/%d partite" % (i, len(missing)))
            save_json(cache_path, cache)

    save_json(cache_path, cache)
    return cache, cache_path


def extract_shots(summary, ev):
    """Estrae shotsOnTarget per casa/trasferta dal boxscore."""
    box = (summary or {}).get("boxscore") or {}
    teams = box.get("teams") or []
    found = {}
    for t in teams:
        tid = str(((t.get("team") or {}).get("id")) or "")
        val = None
        for st in (t.get("statistics") or []):
            if st.get("name") == "shotsOnTarget":
                raw = st.get("displayValue", st.get("value"))
                try:
                    val = int(float(str(raw).strip()))
                except (TypeError, ValueError):
                    val = None
                break
        if tid:
            found[tid] = val
    h = found.get(ev["home"]["id"])
    a = found.get(ev["away"]["id"])
    if h is None and a is None:
        return None
    return {"home": h, "away": a}


# ----------------------------------------------------------------------------
# CALCOLO
# ----------------------------------------------------------------------------

SCOPES = ("total", "home", "away")


def build_match_list(events, shots):
    """Partite concluse, ordinate, con tiri in porta dove disponibili."""
    played, missing = [], []
    for ev in events.values():
        if not ev["completed"]:
            continue
        d = parse_iso(ev["date"])
        s = shots.get(ev["id"]) or {}
        try:
            gh = int(ev["home"]["score"])
            ga = int(ev["away"]["score"])
        except (TypeError, ValueError):
            continue
        rec = {
            "id": ev["id"],
            "dt": d,
            "date": (to_rome(d).strftime("%d/%m/%Y") if d else ""),
            "time": (to_rome(d).strftime("%H:%M") if d else ""),
            "home_id": ev["home"]["id"], "home": ev["home"]["name"], "home_short": ev["home"]["short"],
            "away_id": ev["away"]["id"], "away": ev["away"]["name"], "away_short": ev["away"]["short"],
            "gh": gh, "ga": ga,
            "sth": s.get("home"), "sta": s.get("away"),
        }
        if rec["sth"] is None or rec["sta"] is None:
            missing.append(rec)
        else:
            played.append(rec)
    played.sort(key=lambda r: (r["dt"] or dt.datetime.min))
    missing.sort(key=lambda r: (r["dt"] or dt.datetime.min))
    return played, missing


def points_shots(sth, sta):
    if sth > sta:
        return 3, 0, "V", "P"
    if sth < sta:
        return 0, 3, "P", "V"
    return 1, 1, "N", "N"


def points_goals(gh, ga):
    if gh > ga:
        return 3, 0
    if gh < ga:
        return 0, 3
    return 1, 1


def compute(played):
    """Costruisce tabelle, forma e serie cumulate per i tre scope."""
    teams = {}
    for m in played:
        for tid, name, short in ((m["home_id"], m["home"], m["home_short"]),
                                 (m["away_id"], m["away"], m["away_short"])):
            teams.setdefault(tid, {"id": tid, "name": name, "short": short})

    # eventi per squadra, in ordine cronologico
    per_team = {tid: [] for tid in teams}
    for m in played:
        ph, pa, rh, ra = points_shots(m["sth"], m["sta"])
        gph, gpa = points_goals(m["gh"], m["ga"])
        per_team[m["home_id"]].append({
            "venue": "home", "opp": m["away_short"], "opp_id": m["away_id"],
            "date": m["date"], "sf": m["sth"], "sa": m["sta"], "pts": ph, "res": rh,
            "gf": m["gh"], "ga": m["ga"], "gpts": gph,
        })
        per_team[m["away_id"]].append({
            "venue": "away", "opp": m["home_short"], "opp_id": m["home_id"],
            "date": m["date"], "sf": m["sta"], "sa": m["sth"], "pts": pa, "res": ra,
            "gf": m["ga"], "ga": m["gh"], "gpts": gpa,
        })

    tables, series = {}, {}
    for scope in SCOPES:
        rows, ser = [], {}
        for tid, info in teams.items():
            evs = [e for e in per_team[tid] if scope == "total" or e["venue"] == scope]
            r = {
                "id": tid, "name": info["name"], "short": info["short"],
                "g": len(evs),
                "v": sum(1 for e in evs if e["res"] == "V"),
                "n": sum(1 for e in evs if e["res"] == "N"),
                "p": sum(1 for e in evs if e["res"] == "P"),
                "tpf": sum(e["sf"] for e in evs),
                "tps": sum(e["sa"] for e in evs),
                "pti": sum(e["pts"] for e in evs),
                "real_pti": sum(e["gpts"] for e in evs),
                "gf": sum(e["gf"] for e in evs),
                "gs": sum(e["ga"] for e in evs),
                "form": [{"res": e["res"], "opp": e["opp"], "date": e["date"],
                          "sf": e["sf"], "sa": e["sa"], "venue": e["venue"]}
                         for e in evs[-5:]],
            }
            r["diff"] = r["tpf"] - r["tps"]
            r["real_diff"] = r["gf"] - r["gs"]
            rows.append(r)

            cum, pts = [], 0
            for i, e in enumerate(evs, 1):
                pts += e["pts"]
                cum.append({"n": i, "pts": pts, "opp": e["opp"], "date": e["date"],
                            "res": e["res"], "sf": e["sf"], "sa": e["sa"]})
            ser[tid] = cum

        # classifica tiri in porta
        rows.sort(key=lambda r: (-r["pti"], -r["diff"], -r["tpf"], r["name"]))
        for i, r in enumerate(rows, 1):
            r["pos"] = i
        # classifica reale
        real = sorted(rows, key=lambda r: (-r["real_pti"], -r["real_diff"], -r["gf"], r["name"]))
        for i, r in enumerate(real, 1):
            r["real_pos"] = i
        for r in rows:
            r["delta"] = r["real_pos"] - r["pos"]

        tables[scope] = rows
        series[scope] = ser

    return teams, tables, series


def sanity_checks(played, tables):
    msgs, ok = [], True
    for scope in SCOPES:
        rows = tables[scope]
        tot_pts = sum(r["pti"] for r in rows)
        v = sum(r["v"] for r in rows)
        n = sum(r["n"] for r in rows)
        expected = 3 * v + 1 * n
        if tot_pts != expected:
            ok = False
            msgs.append("[%s] somma punti %d != 3*V+N atteso %d" % (scope, tot_pts, expected))
        if scope == "total":
            if sum(r["g"] for r in rows) != 2 * len(played):
                ok = False
                msgs.append("[total] numero presenze squadra != 2 * partite")
            for r in rows:
                if r["v"] + r["n"] + r["p"] != r["g"]:
                    ok = False
                    msgs.append("[total] %s: V+N+P != G" % r["name"])
    # casa + trasferta = totale
    byid = {r["id"]: r for r in tables["total"]}
    for r in tables["home"]:
        other = next((x for x in tables["away"] if x["id"] == r["id"]), None)
        tot = byid.get(r["id"])
        if other and tot and r["pti"] + other["pti"] != tot["pti"]:
            ok = False
            msgs.append("punti casa+trasferta != totale per %s" % r["name"])
    msgs.insert(0, "Controlli: %s" % ("OK" if ok else "ANOMALIE"))
    return ok, msgs


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0f1115; --panel:#161a21; --panel2:#1c222b; --line:#262d38;
    --txt:#e6e9ef; --dim:#8b95a6; --acc:#4da3ff;
    --win:#22c55e; --drw:#eab308; --los:#ef4444;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
  h1{font-size:20px;margin:0 0 4px}
  h2{font-size:15px;margin:28px 0 10px;color:var(--dim);
     text-transform:uppercase;letter-spacing:.06em;font-weight:600}
  .meta{color:var(--dim);font-size:12.5px;margin-bottom:14px}
  .banner{padding:10px 14px;border-radius:8px;margin:12px 0;font-size:13px;border:1px solid}
  .banner.warn{background:#3a1d1d;border-color:#7f2d2d;color:#ffd4d4}
  .banner.info{background:#1b2838;border-color:#2d4a6b;color:#cfe2ff}
  .tabs{display:flex;gap:6px;margin:14px 0}
  .tabs button{background:var(--panel);color:var(--dim);border:1px solid var(--line);
    padding:7px 16px;border-radius:7px;cursor:pointer;font-size:13px}
  .tabs button.on{background:var(--acc);border-color:var(--acc);color:#061018;font-weight:600}
  table{width:100%;border-collapse:collapse;background:var(--panel);
        border:1px solid var(--line);border-radius:9px;overflow:hidden}
  th,td{padding:7px 9px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
  th{background:var(--panel2);color:var(--dim);font-size:11.5px;
     text-transform:uppercase;letter-spacing:.04em;font-weight:600}
  th:nth-child(2),td:nth-child(2){text-align:left;width:100%}
  tbody tr{cursor:pointer}
  tbody tr:hover{background:#1e2530}
  tbody tr:last-child td{border-bottom:none}
  .pos{color:var(--dim);font-variant-numeric:tabular-nums}
  .pts{font-weight:700}
  .up{color:var(--win)} .down{color:var(--los)} .flat{color:var(--dim)}
  .dots{display:inline-flex;gap:3px}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
  .dot.V{background:var(--win)} .dot.N{background:var(--drw)} .dot.P{background:var(--los)}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
        padding:11px 13px;cursor:pointer}
  .card:hover{border-color:var(--acc)}
  .card .when{color:var(--dim);font-size:12px;margin-bottom:5px}
  .card .vs{font-size:14px;font-weight:600}
  .card .sub{color:var(--dim);font-size:12px;margin-top:5px}
  .mlist{background:var(--panel);border:1px solid var(--line);border-radius:9px}
  .mrow{display:flex;justify-content:space-between;gap:12px;padding:7px 12px;
        border-bottom:1px solid var(--line);cursor:pointer;font-size:13px}
  .mrow:last-child{border-bottom:none}
  .mrow:hover{background:#1e2530}
  .mrow .l{color:var(--dim);min-width:92px}
  .mrow .c{flex:1}
  .mrow .r{color:var(--dim);font-variant-numeric:tabular-nums}
  .ok{color:var(--win)} .ko{color:var(--los)}
  dialog{background:var(--panel);color:var(--txt);border:1px solid var(--line);
         border-radius:12px;padding:0;max-width:820px;width:94%}
  dialog::backdrop{background:rgba(0,0,0,.65)}
  .dlg-h{display:flex;justify-content:space-between;align-items:center;
         padding:13px 16px;border-bottom:1px solid var(--line)}
  .dlg-h b{font-size:15px}
  .dlg-h button{background:none;border:none;color:var(--dim);font-size:22px;cursor:pointer;line-height:1}
  .dlg-b{padding:16px}
  .legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:12.5px;color:var(--dim)}
  .legend span b{color:var(--txt)}
  .foot{color:var(--dim);font-size:12px;margin-top:26px;border-top:1px solid var(--line);padding-top:12px}
  @media(max-width:640px){
    th:nth-child(n+7):nth-child(-n+9),td:nth-child(n+7):nth-child(-n+9){display:none}
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="meta" id="meta"></div>
  <div id="banners"></div>

  <div class="tabs" id="tabs">
    <button data-s="total" class="on">Totale</button>
    <button data-s="home">Casa</button>
    <button data-s="away">Trasferta</button>
  </div>

  <table id="tbl">
    <thead><tr>
      <th>#</th><th>Squadra</th><th>G</th><th>V</th><th>N</th><th>P</th>
      <th>TPF</th><th>TPS</th><th>Diff</th><th>Pti</th><th>Forma</th>
      <th>Reale</th><th>Pti R.</th><th>&Delta;</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  <div class="legend">
    <span><b>TPF/TPS</b> tiri in porta fatti / subiti</span>
    <span><b>Pti</b> punti con la regola dei tiri in porta</span>
    <span><b>Reale</b> posizione e punti della classifica vera</span>
    <span><b>&Delta;</b> posizioni guadagnate rispetto alla classifica vera</span>
    <span><span class="dot V"></span> V &nbsp; <span class="dot N"></span> N &nbsp; <span class="dot P"></span> P</span>
  </div>

  <h2>Partite da giocare</h2>
  <div class="cards" id="upcoming"></div>

  <h2>Partite giocate</h2>
  <div class="mlist" id="played"></div>

  <div id="missing"></div>

  <div class="foot" id="foot"></div>
</div>

<dialog id="dlg">
  <div class="dlg-h"><b id="dlg-t"></b><button onclick="document.getElementById('dlg').close()">&times;</button></div>
  <div class="dlg-b"><canvas id="cv" height="150"></canvas><div id="dlg-x" class="legend"></div></div>
</dialog>

<script>
const DATA = __DATA__;
let scope = "total";
let chart = null;
const COL = ["#4da3ff", "#ff9f4d"];

function rowsOf(s){ return DATA.tables[s]; }
function teamRow(s, id){ return rowsOf(s).find(r => r.id === id); }

function dots(form){
  if(!form.length) return '<span style="color:var(--dim)">-</span>';
  return '<span class="dots">' + form.map(f =>
    `<span class="dot ${f.res}" title="${f.date} ${f.venue==='home'?'casa':'trasf.'} vs ${f.opp} — TP ${f.sf}-${f.sa}"></span>`
  ).join('') + '</span>';
}

function renderTable(){
  const tb = document.querySelector("#tbl tbody");
  tb.innerHTML = rowsOf(scope).map(r => {
    const d = r.delta;
    const cls = d > 0 ? "up" : (d < 0 ? "down" : "flat");
    const txt = d > 0 ? "+"+d : (d === 0 ? "=" : d);
    return `<tr onclick="showTeam('${r.id}')">
      <td class="pos">${r.pos}</td><td>${r.name}</td>
      <td>${r.g}</td><td>${r.v}</td><td>${r.n}</td><td>${r.p}</td>
      <td>${r.tpf}</td><td>${r.tps}</td><td>${r.diff>0?'+':''}${r.diff}</td>
      <td class="pts">${r.pti}</td><td>${dots(r.form)}</td>
      <td class="pos">${r.real_pos}</td><td>${r.real_pti}</td>
      <td class="${cls}">${txt}</td></tr>`;
  }).join('');
}

function renderUpcoming(){
  const el = document.getElementById("upcoming");
  if(!DATA.upcoming.length){ el.innerHTML = '<div class="card">Nessuna partita in programma nei dati.</div>'; return; }
  el.innerHTML = DATA.upcoming.map(m => {
    const h = teamRow(scope, m.home_id), a = teamRow(scope, m.away_id);
    const s = (r) => r ? `${r.pos}&ordm; · ${r.pti} pti` : "n.d.";
    return `<div class="card" onclick="showMatch('${m.home_id}','${m.away_id}','${m.home} - ${m.away}')">
      <div class="when">${m.date} ${m.time}</div>
      <div class="vs">${m.home} - ${m.away}</div>
      <div class="sub">TP: ${s(h)} &nbsp;|&nbsp; ${s(a)}</div></div>`;
  }).join('');
}

function renderPlayed(){
  const el = document.getElementById("played");
  const rows = DATA.played.slice().reverse();
  el.innerHTML = rows.map(m => {
    const esito = m.sth > m.sta ? "V casa" : (m.sth < m.sta ? "V trasferta" : "pari");
    const realeC = m.gh > m.ga ? "V casa" : (m.gh < m.ga ? "V trasferta" : "pari");
    const cls = esito === realeC ? "ok" : "ko";
    return `<div class="mrow" onclick="showMatch('${m.home_id}','${m.away_id}','${m.home} - ${m.away}')">
      <span class="l">${m.date}</span>
      <span class="c">${m.home} ${m.gh}-${m.ga} ${m.away}</span>
      <span class="r">TP ${m.sth}-${m.sta} &rarr; <span class="${cls}">${esito}</span></span></div>`;
  }).join('');
}

function drawChart(title, datasets, maxN){
  document.getElementById("dlg-t").innerHTML = title;
  if(chart) chart.destroy();
  const labels = Array.from({length: maxN}, (_, i) => i + 1);
  chart = new Chart(document.getElementById("cv"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, interaction: {mode:"index", intersect:false},
      plugins: {
        legend: {labels:{color:"#e6e9ef", boxWidth:12}},
        tooltip: {callbacks:{ afterLabel: (c) => {
          const p = c.dataset.meta && c.dataset.meta[c.dataIndex];
          return p ? `${p.date} vs ${p.opp} — TP ${p.sf}-${p.sa} (${p.res})` : "";
        }}}
      },
      scales: {
        x: {title:{display:true,text:"partita n.",color:"#8b95a6"},
            ticks:{color:"#8b95a6"}, grid:{color:"#262d38"}},
        y: {title:{display:true,text:"punti cumulati (tiri in porta)",color:"#8b95a6"},
            beginAtZero:true, ticks:{color:"#8b95a6"}, grid:{color:"#262d38"}}
      }
    }
  });
  document.getElementById("dlg").showModal();
}

function dsFor(id, color){
  const s = DATA.series[scope][id] || [];
  const r = teamRow(scope, id);
  return {
    label: (r ? r.name : id) + " — " + (r ? r.pti + " pti" : ""),
    data: s.map(p => p.pts), meta: s,
    borderColor: color, backgroundColor: color,
    tension: .2, pointRadius: 3, borderWidth: 2
  };
}

function showTeam(id){
  const r = teamRow(scope, id);
  const s = DATA.series[scope][id] || [];
  drawChart(`${r.name} — ${scopeLabel()}`, [dsFor(id, COL[0])], Math.max(1, s.length));
  document.getElementById("dlg-x").innerHTML =
    `<span><b>${r.pti}</b> pti TP (${r.pos}&ordm;)</span>
     <span><b>${r.real_pti}</b> pti reali (${r.real_pos}&ordm;)</span>
     <span>TP <b>${r.tpf}</b>-<b>${r.tps}</b></span>
     <span>${dots(r.form)}</span>`;
}

function showMatch(hid, aid, title){
  const sh = DATA.series[scope][hid] || [], sa = DATA.series[scope][aid] || [];
  drawChart(`${title} — ${scopeLabel()}`,
            [dsFor(hid, COL[0]), dsFor(aid, COL[1])],
            Math.max(1, sh.length, sa.length));
  const h = teamRow(scope, hid), a = teamRow(scope, aid);
  const b = (r) => r ? `<span><b>${r.name}</b> ${r.pos}&ordm; TP (${r.pti}) · ${r.real_pos}&ordm; reale (${r.real_pti}) ${dots(r.form)}</span>` : "";
  document.getElementById("dlg-x").innerHTML = b(h) + b(a);
}

function scopeLabel(){
  return {total:"totale", home:"solo casa", away:"solo trasferta"}[scope];
}

function renderAll(){ renderTable(); renderUpcoming(); renderPlayed(); }

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); scope = b.dataset.s; renderAll();
});

document.getElementById("meta").innerHTML = DATA.meta_html;
document.getElementById("banners").innerHTML = DATA.banners_html;
document.getElementById("missing").innerHTML = DATA.missing_html;
document.getElementById("foot").innerHTML = DATA.foot_html;
renderAll();
</script>
</body>
</html>
"""


def render(path, title, data):
    html = HTML_TEMPLATE.replace("__TITLE__", title)
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    # Se cambia solo l'orario "aggiornato il ...", non riscrivere il file:
    # cosi' il workflow giornaliero non produce commit inutili.
    stamp = re.compile(r"aggiornato il \d\d/\d\d/\d{4} \d\d:\d\d")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
        if stamp.sub("", old) == stamp.sub("", html):
            log("    nessuna variazione nei dati: index.html invariato")
            return
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Classifica per tiri in porta")
    ap.add_argument("--league", default="ita.1", help="codice ESPN (ita.1, eng.1, esp.1, ger.1, fra.1)")
    ap.add_argument("--full", action="store_true", help="riscarica tutta la stagione")
    ap.add_argument("--offline", action="store_true", help="usa solo la cache locale")
    ap.add_argument("--out", default=OUT_HTML, help="file HTML di output")
    args = ap.parse_args()

    ensure_dirs()
    league = args.league
    label = LEAGUE_LABELS.get(league, league)
    log("== Classifica tiri in porta — %s ==" % label)

    log("[1/4] indice partite")
    events, _, _ = fetch_events(league, full=args.full, offline=args.offline)
    if not events:
        log("Nessuna partita trovata. Interrompo.")
        return 1

    log("[2/4] tiri in porta")
    shots, _ = fetch_stats(league, events, full=args.full, offline=args.offline)

    log("[3/4] calcolo")
    played, missing = build_match_list(events, shots)
    if not played:
        log("Nessuna partita con tiri in porta disponibili. Interrompo.")
        return 1
    teams, tables, series = compute(played)
    ok, msgs = sanity_checks(played, tables)
    for m in msgs:
        log("    " + m)

    # prossime partite: primo gruppo non concluso
    upcoming = []
    for ev in events.values():
        if ev["completed"]:
            continue
        d = parse_iso(ev["date"])
        if d is None:
            continue
        upcoming.append({
            "dt": d,
            "date": to_rome(d).strftime("%d/%m/%Y"),
            "time": to_rome(d).strftime("%H:%M"),
            "home_id": ev["home"]["id"], "home": ev["home"]["name"],
            "away_id": ev["away"]["id"], "away": ev["away"]["name"],
        })
    upcoming.sort(key=lambda x: x["dt"])
    if upcoming:
        first = upcoming[0]["dt"].date()
        upcoming = [u for u in upcoming if (u["dt"].date() - first).days <= 4][:12]
    for u in upcoming:
        u.pop("dt", None)

    # banner e metadati
    last = played[-1]
    last_date = last["dt"].date() if last["dt"] else None
    today = dt.date.today()
    lag = (today - last_date).days if last_date else None

    banners = []
    stale = [u for u in upcoming if _to_date(u["date"]) and _to_date(u["date"]) < today]
    if stale:
        banners.append(('warn',
            "Attenzione: %d partite con data gia' passata risultano ancora non concluse nei dati. "
            "I risultati piu' recenti potrebbero mancare." % len(stale)))
    if lag is not None and lag > 8:
        banners.append(('warn',
            "Ultima partita nei dati: %s (%d giorni fa). La fonte potrebbe essere indietro." %
            (last["date"], lag)))
    if missing:
        banners.append(('info',
            "%d partite escluse dal calcolo perche' prive dei tiri in porta." % len(missing)))

    meta_html = ("Stagione in corso &middot; <b>%d</b> partite incluse &middot; "
                 "ultima: <b>%s</b> (%s) &middot; aggiornato il %s") % (
        len(played), last["date"],
        esc("%s %d-%d %s" % (last["home"], last["gh"], last["ga"], last["away"])),
        dt.datetime.now().strftime("%d/%m/%Y %H:%M"))

    missing_html = ""
    if missing:
        rows = "".join(
            '<div class="mrow"><span class="l">%s</span><span class="c">%s %d-%d %s</span>'
            '<span class="r">tiri in porta non disponibili</span></div>' %
            (m["date"], esc(m["home"]), m["gh"], m["ga"], esc(m["away"]))
            for m in missing)
        missing_html = '<h2>Dati mancanti</h2><div class="mlist">%s</div>' % rows

    data = {
        "tables": tables,
        "series": series,
        "played": [{k: v for k, v in m.items() if k != "dt"} for m in played],
        "upcoming": upcoming,
        "meta_html": meta_html,
        "banners_html": "".join('<div class="banner %s">%s</div>' % (c, esc(t)) for c, t in banners),
        "missing_html": missing_html,
        "foot_html": ("Fonte: API pubbliche ESPN. Regola: piu' tiri in porta = 3 punti, "
                      "pari = 1 punto a testa. Ordinamento a pari punti: differenza tiri in porta, "
                      "poi tiri in porta fatti. Controlli interni: %s." % ("OK" if ok else "ANOMALIE")),
    }

    log("[4/4] genero %s" % args.out)
    render(args.out, "Classifica tiri in porta — %s" % label, data)
    save_json(os.path.join(PROC_DIR, "tables.json"), tables)

    log("")
    log("Fatto. Partite incluse: %d, escluse: %d. Ultima: %s." % (len(played), len(missing), last["date"]))
    if banners:
        for _, t in banners:
            log("  ! " + t)
    log("Apri: file://%s" % os.path.abspath(args.out))
    return 0 if ok else 2


def _to_date(ddmmyyyy):
    try:
        return dt.datetime.strptime(ddmmyyyy, "%d/%m/%Y").date()
    except Exception:                                # noqa: BLE001
        return None


if __name__ == "__main__":
    sys.exit(main())
