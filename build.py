#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Classifiche alternative per campionato — punti assegnati in base a una statistica
(oggi: TIRI IN PORTA).

Regola: in ogni partita la squadra con piu' tiri in porta "vince" (3 punti).
Tiri in porta pari = pareggio (1 punto a testa). Solo partite concluse.

Fonte dati: API pubbliche ESPN (nessuna API key).
  - elenco partite:  site.api.espn.com     /apis/site/v2/sports/soccer/<lega>/scoreboard?dates=YYYYMMDD
  - statistiche:     site.web.api.espn.com /apis/site/v2/sports/soccer/<lega>/summary?event=<id>
  - controllo G:     site.api.espn.com     /apis/v2/sports/soccer/<lega>/standings

Output:
  index.html                           home, una card per campionato/statistica
  <league-slug>/<stat-slug>.html       una pagina per ogni coppia campionato-statistica
  data/raw/<league-slug>/              cache (events.json + <stat-slug>.json)
  data/processed/<league-slug>/<stat-slug>/{tables,meta}.json

Uso:
    python3 build.py                     # tutti i campionati, aggiornamento incrementale
    python3 build.py --league eng.1      # un solo campionato (codice ESPN o slug)
    python3 build.py --full              # riscarica tutta la stagione
    python3 build.py --offline           # ricostruisce dalla cache, senza rete

Codice di uscita: 0 tutto ok; 1 almeno un campionato fallito o con dati parziali
(gli altri campionati vengono comunque pubblicati); 2 controlli interni in anomalia.

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
PROC_DIR = os.path.join(DATA_DIR, "processed")
OUT_HOME = os.path.join(BASE_DIR, "index.html")

# Registro campionati: aggiungerne uno = una riga.
LEAGUES = [
    {"code": "ita.1", "name": "Serie A",        "slug": "serie-a"},
    {"code": "eng.1", "name": "Premier League", "slug": "premier-league"},
]

# Registro statistiche. "id" e' il nome ESPN in boxscore.teams[].statistics[].name.
# implemented=False -> compare nel menu come "prossimamente", non cliccabile.
# Per implementarne una: implemented=True (le pagine vengono generate da sole).
# "abbr" abbrevia la statistica nelle colonne (abbr+F fatti, abbr+S subiti),
# "unit" e' il nome esteso usato nei testi.
STATS = [
    {"id": "shotsOnTarget", "name": "Tiri in porta",   "slug": "tiri-in-porta",
     "abbr": "TP", "unit": "tiri in porta", "implemented": True},
    {"id": "totalShots",    "name": "Tiri totali",     "slug": "tiri-totali",
     "abbr": "TT", "unit": "tiri totali", "implemented": False},
    {"id": "wonCorners",    "name": "Calci d'angolo",  "slug": "calci-d-angolo",
     "abbr": "CA", "unit": "calci d'angolo", "implemented": False},
]

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"
SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event}"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) classifiche-alternative/1.0"
REQUEST_PAUSE = 0.35          # secondi fra una richiesta e l'altra
LOOKBACK_REFRESH_DAYS = 10    # giorni recenti sempre riscaricati
STALE_HOURS = 4               # calcio d'inizio passato da oltre N ore e non conclusa -> da verificare


# ----------------------------------------------------------------------------
# UTILITY
# ----------------------------------------------------------------------------

FAILED_REQUESTS = []          # url con richiesta fallita in modo definitivo


def log(msg):
    print(msg, flush=True)


def http_json(url, retries=3, timeout=25):
    """GET -> dict. Ritorna None su errore definitivo (registrato in FAILED_REQUESTS)."""
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
                break
        except Exception as e:                      # noqa: BLE001
            last = str(e)
        time.sleep(1.2 * (attempt + 1))
    log("    ! richiesta fallita (%s): %s" % (last, url))
    FAILED_REQUESTS.append(url)
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


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


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


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def raw_dir(league):
    return os.path.join(RAW_DIR, league["slug"])


def proc_dir(league, stat):
    return os.path.join(PROC_DIR, league["slug"], stat["slug"])


def page_rel(league, stat):
    """Percorso della pagina relativo alla radice del sito."""
    return "%s/%s.html" % (league["slug"], stat["slug"])


def implemented_stats():
    return [s for s in STATS if s["implemented"]]


# ----------------------------------------------------------------------------
# DOWNLOAD
# ----------------------------------------------------------------------------

def fetch_events(league, full=False, offline=False):
    """Scarica/aggiorna l'indice partite. Ritorna dict event_id -> evento."""
    index_path = os.path.join(raw_dir(league), "events.json")
    store = load_json(index_path, default={"events": {}, "days": {}}) or {"events": {}, "days": {}}
    store.setdefault("events", {})
    store.setdefault("days", {})

    if offline:
        log("  modalita' offline: uso la cache (%d partite)" % len(store["events"]))
        return store["events"]

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
        data = http_json(SCOREBOARD.format(league=league["code"], date=key))
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
    return store["events"]


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
            "status_name": status.get("name"),            # es. STATUS_HALFTIME
            "status_detail": status.get("description") or status.get("detail"),
            "home": home,
            "away": away,
        }
    except Exception:                                    # noqa: BLE001
        return None


def fetch_stats(league, stat, events, full=False, offline=False):
    """Scarica la statistica per le partite concluse. Ritorna dict event_id -> {home,away}."""
    cache_path = os.path.join(raw_dir(league), "%s.json" % stat["slug"])
    cache = load_json(cache_path, default={}) or {}

    finished = [e for e in events.values() if e["completed"]]
    if offline:
        log("  modalita' offline: %d partite con %s in cache" % (len(cache), stat["unit"]))
        return cache

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
        data = http_json(SUMMARY.format(league=league["code"], event=ev["id"]))
        time.sleep(REQUEST_PAUSE)
        if data is None:
            # richiesta fallita: non e' "statistica assente", la partita resta da riprovare
            if ev["id"] not in cache:
                cache[ev["id"]] = {"home": None, "away": None}
            continue
        shots = extract_stat(data, ev, stat)
        cache[ev["id"]] = shots if shots else {"home": None, "away": None}
        if i % 20 == 0 or i == len(missing):
            log("    %d/%d partite" % (i, len(missing)))
            save_json(cache_path, cache)

    save_json(cache_path, cache)
    return cache


def extract_stat(summary, ev, stat):
    """Estrae la statistica (stat["id"]) per casa/trasferta dal boxscore."""
    box = (summary or {}).get("boxscore") or {}
    teams = box.get("teams") or []
    found = {}
    for t in teams:
        tid = str(((t.get("team") or {}).get("id")) or "")
        val = None
        for st in (t.get("statistics") or []):
            if st.get("name") == stat["id"]:
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


def fetch_standings_games(league):
    """Partite giocate per squadra secondo ESPN standings: {team_id: G}, o None se non leggibile."""
    data = http_json(STANDINGS.format(league=league["code"]))
    if not data:
        return None
    out = {}
    try:
        for child in data.get("children") or []:
            for ent in (child.get("standings") or {}).get("entries") or []:
                tid = str((ent.get("team") or {}).get("id") or "")
                for st in ent.get("stats") or []:
                    if st.get("name") == "gamesPlayed":
                        out[tid] = int(float(st.get("value")))
    except (TypeError, ValueError):
        return None
    return out or None


# ----------------------------------------------------------------------------
# CALCOLO
# ----------------------------------------------------------------------------

SCOPES = ("total", "home", "away")


def build_match_list(events, shots):
    """Partite concluse, ordinate, con la statistica dove disponibile.

    Ritorna (played, missing, noscore): noscore = concluse ma senza risultato leggibile
    (mai scartate in silenzio: finiscono in "Dati da verificare").
    """
    played, missing, noscore = [], [], []
    for ev in events.values():
        if not ev["completed"]:
            continue
        d = parse_iso(ev["date"])
        s = shots.get(ev["id"]) or {}
        try:
            gh = int(ev["home"]["score"])
            ga = int(ev["away"]["score"])
        except (TypeError, ValueError):
            noscore.append(ev)
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
    return played, missing, noscore


def find_to_verify(events, noscore, now):
    """Partite da verificare: calcio d'inizio passato da oltre STALE_HOURS e non concluse,
    piu' le concluse prive di risultato. Non entrano nelle classifiche."""
    limit = now - dt.timedelta(hours=STALE_HOURS)
    out = []
    for ev in events.values():
        if ev["completed"]:
            continue
        d = parse_iso(ev["date"])
        if d is None or d >= limit:
            continue
        out.append(verify_record(ev, d, "non conclusa"))
    for ev in noscore:
        out.append(verify_record(ev, parse_iso(ev["date"]), "conclusa senza risultato leggibile"))
    out.sort(key=lambda r: (r["sort"], r["id"]))
    for r in out:
        r.pop("sort")
    return out


def verify_record(ev, d, reason):
    sc_h, sc_a = ev["home"].get("score"), ev["away"].get("score")
    score = "%s-%s" % (sc_h, sc_a) if sc_h not in (None, "") and sc_a not in (None, "") else None
    r = to_rome(d) if d else None
    return {
        "id": ev["id"],
        "sort": d or dt.datetime.min,
        "date": r.strftime("%d/%m/%Y") if r else "?",
        "time": r.strftime("%H:%M") if r else "",
        "home": ev["home"]["name"], "away": ev["away"]["name"],
        "score": score,
        "status": ev.get("status_detail") or ev.get("status_name") or ev.get("state") or "?",
        "reason": reason,
    }


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
    """Costruisce tabelle, forma e serie cumulate per i tre scope.

    Nota: le chiavi tpf/tps/sf/sa restano quelle storiche (statistica fatta/subita),
    qualunque sia la statistica.
    """
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

        # classifica per statistica
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


def compare_standings(league, tables, standings, verify):
    """Confronta G per squadra con ESPN standings. Ritorna lista di messaggi (vuota = ok)."""
    if standings is None:
        return None
    msgs = []
    pending = set()
    for v in verify:
        pending.add(v["home"])
        pending.add(v["away"])
    for r in tables["total"]:
        espn = standings.get(r["id"])
        if espn is None:
            msgs.append("%s: assente nello standings ESPN" % r["name"])
        elif espn != r["g"]:
            extra = " (ha una partita in 'Dati da verificare')" if r["name"] in pending else ""
            msgs.append("%s: G nostro %d, ESPN %d%s" % (r["name"], r["g"], espn, extra))
    return msgs


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

CSS = r"""
  :root{
    --bg:#0f1115; --panel:#161a21; --panel2:#1c222b; --line:#262d38;
    --txt:#e6e9ef; --dim:#8b95a6; --acc:#4da3ff;
    --win:#22c55e; --drw:#eab308; --los:#ef4444;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  a{color:inherit}
  .wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
  h1{font-size:20px;margin:0 0 4px}
  h2{font-size:15px;margin:28px 0 10px;color:var(--dim);
     text-transform:uppercase;letter-spacing:.06em;font-weight:600}
  .meta{color:var(--dim);font-size:12.5px;margin-bottom:14px}
  .banner{padding:10px 14px;border-radius:8px;margin:12px 0;font-size:13px;border:1px solid}
  .banner.warn{background:#3a1d1d;border-color:#7f2d2d;color:#ffd4d4}
  .banner.info{background:#1b2838;border-color:#2d4a6b;color:#cfe2ff}
  .banner.verify{background:#3a2e12;border-color:#8a6a1c;color:#ffe9b3}
  .banner ul{margin:8px 0 0;padding-left:18px}
  .banner li{margin:3px 0}
  .nav{background:var(--panel);border-bottom:1px solid var(--line)}
  .nav .in{max-width:1180px;margin:0 auto;padding:8px 16px;display:flex;flex-direction:column;gap:6px}
  .nav .row{display:flex;gap:6px;align-items:center;overflow-x:auto;-webkit-overflow-scrolling:touch;
            scrollbar-width:none}
  .nav .row::-webkit-scrollbar{display:none}
  .nav .lab{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em;
            min-width:74px;flex:none}
  .nav a.it,.nav span.it{flex:none;padding:6px 12px;border-radius:7px;border:1px solid var(--line);
            white-space:nowrap;font-size:13px;text-decoration:none;background:var(--panel2);color:var(--txt)}
  .nav a.it:hover{border-color:var(--acc)}
  .nav a.it.on{background:var(--acc);border-color:var(--acc);color:#061018;font-weight:600}
  .nav span.soon{color:var(--dim);opacity:.65;border-style:dashed;cursor:not-allowed}
  .nav span.soon small{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;margin-left:4px}
  .tabs{display:flex;gap:6px;margin:14px 0}
  .tabs button{background:var(--panel);color:var(--dim);border:1px solid var(--line);
    padding:7px 16px;border-radius:7px;cursor:pointer;font-size:13px}
  .tabs button.on{background:var(--acc);border-color:var(--acc);color:#061018;font-weight:600}
  .tw{overflow-x:auto;border-radius:9px}
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
        padding:11px 13px;cursor:pointer;display:block;text-decoration:none;color:inherit}
  .card:hover{border-color:var(--acc)}
  .card .when{color:var(--dim);font-size:12px;margin-bottom:5px}
  .card .vs{font-size:14px;font-weight:600}
  .card .sub{color:var(--dim);font-size:12px;margin-top:5px}
  .card.soon{cursor:not-allowed;opacity:.55;border-style:dashed}
  .card.soon:hover{border-color:var(--line)}
  .mlist{background:var(--panel);border:1px solid var(--line);border-radius:9px}
  .mrow{display:flex;justify-content:space-between;gap:12px;padding:7px 12px;
        border-bottom:1px solid var(--line);cursor:pointer;font-size:13px}
  .mrow.static{cursor:default}
  .mrow:last-child{border-bottom:none}
  .mrow:hover{background:#1e2530}
  .mrow .l{color:var(--dim);min-width:92px}
  .mrow .c{flex:1}
  .mrow .r{color:var(--dim);font-variant-numeric:tabular-nums}
  .ok{color:var(--win)} .ko{color:var(--los)}
  details.box{margin-top:28px}
  details.box>summary{cursor:pointer;font-size:15px;color:var(--dim);text-transform:uppercase;
     letter-spacing:.06em;font-weight:600;padding:6px 0;margin-bottom:6px;list-style:none}
  details.box>summary::-webkit-details-marker{display:none}
  details.box>summary::before{content:"\25B8\00a0"}
  details.box[open]>summary::before{content:"\25BE\00a0"}
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
  .lg{margin-top:26px}
  .lg h2{margin-top:0}
  @media(max-width:640px){
    .wrap{padding:14px 16px 50px}
    th:nth-child(n+7):nth-child(-n+9),td:nth-child(n+7):nth-child(-n+9){display:none}
    th:nth-child(11),td:nth-child(11),th:nth-child(13),td:nth-child(13){display:none}
    th,td{padding:7px 4px}
    td:nth-child(2){white-space:normal;min-width:96px}
    th:first-child,td:first-child{padding-left:8px}
    th:last-child,td:last-child{padding-right:8px}
    .mrow{flex-wrap:wrap;gap:2px 12px}
    .mrow .l{min-width:0}
    .nav .lab{min-width:62px}
  }
"""

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>__CSS__</style>
</head>
<body>
__NAV__
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="meta" id="meta"></div>
  <div id="banners"></div>
  <div id="verify"></div>

  <div class="tabs" id="tabs">
    <button data-s="total" class="on">Totale</button>
    <button data-s="home">Casa</button>
    <button data-s="away">Trasferta</button>
  </div>

  <div class="tw"><table id="tbl">
    <thead><tr>
      <th>#</th><th>Squadra</th><th>G</th><th>V</th><th>N</th><th>P</th>
      <th>__ABBR__F</th><th>__ABBR__S</th><th>Diff</th><th>Pti</th><th>Forma</th>
      <th>Reale</th><th>Pti R.</th><th>&Delta;</th>
    </tr></thead>
    <tbody></tbody>
  </table></div>
  <div class="legend">
    <span><b>__ABBR__F/__ABBR__S</b> __UNIT__ fatti / subiti</span>
    <span><b>Pti</b> punti con la regola: piu' __UNIT__ = 3 punti</span>
    <span><b>Reale</b> posizione e punti della classifica vera</span>
    <span><b>&Delta;</b> posizioni guadagnate rispetto alla classifica vera</span>
    <span><span class="dot V"></span> V &nbsp; <span class="dot N"></span> N &nbsp; <span class="dot P"></span> P</span>
  </div>

  <h2>Partite da giocare</h2>
  <div class="cards" id="upcoming"></div>

  <details class="box" id="playedBox">
    <summary>Partite giocate (__NPLAYED__)</summary>
    <div class="mlist" id="played"></div>
  </details>

  <div id="missing"></div>

  <div class="foot" id="foot"></div>
</div>

<dialog id="dlg">
  <div class="dlg-h"><b id="dlg-t"></b><button onclick="document.getElementById('dlg').close()">&times;</button></div>
  <div class="dlg-b"><canvas id="cv" height="150"></canvas><div id="dlg-x" class="legend"></div></div>
</dialog>

<script>
const DATA = __DATA__;
const A = DATA.stat.abbr;
let scope = "total";
let chart = null;
const COL = ["#4da3ff", "#ff9f4d"];

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function rowsOf(s){ return DATA.tables[s]; }
function teamRow(s, id){ return rowsOf(s).find(r => r.id === id); }
function nameOf(id){ return DATA.names[id] || id; }

function dots(form){
  if(!form.length) return '<span style="color:var(--dim)">-</span>';
  return '<span class="dots">' + form.map(f =>
    `<span class="dot ${f.res}" title="${esc(f.date)} ${f.venue==='home'?'casa':'trasf.'} vs ${esc(f.opp)} — ${A} ${f.sf}-${f.sa}"></span>`
  ).join('') + '</span>';
}

function renderTable(){
  const tb = document.querySelector("#tbl tbody");
  tb.innerHTML = rowsOf(scope).map(r => {
    const d = r.delta;
    const cls = d > 0 ? "up" : (d < 0 ? "down" : "flat");
    const txt = d > 0 ? "+"+d : (d === 0 ? "=" : d);
    return `<tr onclick="showTeam('${r.id}')">
      <td class="pos">${r.pos}</td><td>${esc(r.name)}</td>
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
    return `<div class="card" onclick="showMatch('${m.home_id}','${m.away_id}')">
      <div class="when">${m.date} ${m.time}</div>
      <div class="vs">${esc(m.home)} - ${esc(m.away)}</div>
      <div class="sub">${A}: ${s(h)} &nbsp;|&nbsp; ${s(a)}</div></div>`;
  }).join('');
}

function renderPlayed(){
  const el = document.getElementById("played");
  const rows = DATA.played.slice().reverse();
  el.innerHTML = rows.map(m => {
    const esito = m.sth > m.sta ? "V casa" : (m.sth < m.sta ? "V trasferta" : "pari");
    const realeC = m.gh > m.ga ? "V casa" : (m.gh < m.ga ? "V trasferta" : "pari");
    const cls = esito === realeC ? "ok" : "ko";
    return `<div class="mrow" onclick="showMatch('${m.home_id}','${m.away_id}')">
      <span class="l">${m.date}</span>
      <span class="c">${esc(m.home)} ${m.gh}-${m.ga} ${esc(m.away)}</span>
      <span class="r">${A} ${m.sth}-${m.sta} &rarr; <span class="${cls}">${esito}</span></span></div>`;
  }).join('');
}

function drawChart(title, datasets, maxN){
  document.getElementById("dlg-t").textContent = title;
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
          return p ? `${p.date} vs ${p.opp} — ${A} ${p.sf}-${p.sa} (${p.res})` : "";
        }}}
      },
      scales: {
        x: {title:{display:true,text:"partita n.",color:"#8b95a6"},
            ticks:{color:"#8b95a6"}, grid:{color:"#262d38"}},
        y: {title:{display:true,text:"punti cumulati ("+DATA.stat.unit+")",color:"#8b95a6"},
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
    `<span><b>${r.pti}</b> pti ${A} (${r.pos}&ordm;)</span>
     <span><b>${r.real_pti}</b> pti reali (${r.real_pos}&ordm;)</span>
     <span>${A} <b>${r.tpf}</b>-<b>${r.tps}</b></span>
     <span>${dots(r.form)}</span>`;
}

function showMatch(hid, aid){
  const sh = DATA.series[scope][hid] || [], sa = DATA.series[scope][aid] || [];
  drawChart(`${nameOf(hid)} - ${nameOf(aid)} — ${scopeLabel()}`,
            [dsFor(hid, COL[0]), dsFor(aid, COL[1])],
            Math.max(1, sh.length, sa.length));
  const h = teamRow(scope, hid), a = teamRow(scope, aid);
  const b = (r) => r ? `<span><b>${esc(r.name)}</b> ${r.pos}&ordm; ${A} (${r.pti}) · ${r.real_pos}&ordm; reale (${r.real_pti}) ${dots(r.form)}</span>` : "";
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
document.getElementById("verify").innerHTML = DATA.verify_html;
document.getElementById("missing").innerHTML = DATA.missing_html;
document.getElementById("foot").innerHTML = DATA.foot_html;
renderAll();
</script>
</body>
</html>
"""

HOME_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Classifiche alternative</title>
<style>__CSS__</style>
</head>
<body>
__NAV__
<div class="wrap">
  <h1>Classifiche alternative</h1>
  <div class="meta">I punti non dipendono dai gol ma da una statistica di partita: pi&ugrave; tiri in porta = 3 punti,
  pari = 1 a testa. Scegli campionato e statistica.</div>
__BODY__
  <div class="foot">Fonte: API pubbliche ESPN. Solo partite concluse.</div>
</div>
</body>
</html>
"""


def nav_html(prefix, cur_league=None, cur_stat=None):
    """Menu a due livelli. prefix = '' dalla radice, '../' dalle sottocartelle."""
    first = implemented_stats()[0]
    home_on = cur_league is None
    items = ['<a class="it%s" href="%sindex.html"%s>Home</a>' %
             (" on" if home_on else "", prefix, ' aria-current="page"' if home_on else "")]
    for lg in LEAGUES:
        st = cur_stat or first
        on = cur_league is not None and lg["slug"] == cur_league["slug"]
        items.append('<a class="it%s" href="%s%s"%s>%s</a>' % (
            " on" if on else "", prefix, page_rel(lg, st),
            ' aria-current="page"' if on else "", esc(lg["name"])))
    html = ['<nav class="nav"><div class="in">',
            '<div class="row"><span class="lab">Campionato</span>%s</div>' % "".join(items)]
    if cur_league is not None:
        sitems = []
        for st in STATS:
            if st["implemented"]:
                on = cur_stat is not None and st["slug"] == cur_stat["slug"]
                sitems.append('<a class="it%s" href="%s%s"%s>%s</a>' % (
                    " on" if on else "", prefix, page_rel(cur_league, st),
                    ' aria-current="page"' if on else "", esc(st["name"])))
            else:
                sitems.append('<span class="it soon" aria-disabled="true" title="Prossimamente">'
                              '%s<small>prossimamente</small></span>' % esc(st["name"]))
        html.append('<div class="row"><span class="lab">Statistica</span>%s</div>' % "".join(sitems))
    html.append("</div></nav>")
    return "".join(html)


STAMP = re.compile(r"aggiornato il \d\d/\d\d/\d{4} \d\d:\d\d")


def write_page(path, html):
    """Scrive solo se cambia qualcosa oltre all'orario 'aggiornato il ...':
    cosi' il workflow giornaliero non produce commit inutili."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
        if STAMP.sub("", old) == STAMP.sub("", html):
            log("    %s: nessuna variazione" % os.path.relpath(path, BASE_DIR))
            return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log("    scritto %s" % os.path.relpath(path, BASE_DIR))
    return True


def render_page(league, stat, data, nplayed):
    title = "Classifica %s — %s" % (stat["unit"], league["name"])
    html = PAGE_TEMPLATE
    # il JSON va inserito per ultimo: non deve subire altre sostituzioni
    for k, v in (("__CSS__", CSS), ("__NAV__", nav_html("../", league, stat)),
                 ("__TITLE__", esc(title)), ("__ABBR__", stat["abbr"]),
                 ("__UNIT__", esc(stat["unit"])), ("__NPLAYED__", str(nplayed))):
        html = html.replace(k, v)
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    return write_page(os.path.join(BASE_DIR, league["slug"], "%s.html" % stat["slug"]), html)


def render_home(metas, failed_slugs):
    """Home: una card per ogni campionato/statistica. metas[(league,stat)] da data/processed."""
    parts = []
    for lg in LEAGUES:
        cards = []
        for st in STATS:
            if not st["implemented"]:
                cards.append('<div class="card soon" aria-disabled="true"><div class="when">%s</div>'
                             '<div class="vs">%s</div><div class="sub">Prossimamente</div></div>'
                             % (esc(lg["name"]), esc(st["name"])))
                continue
            m = metas.get((lg["slug"], st["slug"]))
            if m:
                sub = "%d partite incluse &middot; 1&ordm; %s (%d pti)" % (
                    m["included"], esc(m["leader"]), m["leader_pti"])
                if m.get("to_verify"):
                    sub += " &middot; %d da verificare" % m["to_verify"]
            else:
                sub = "Dati non ancora disponibili"
            if lg["slug"] in failed_slugs:
                sub += ' <span class="ko">&middot; ultimo aggiornamento non riuscito</span>'
            cards.append('<a class="card" href="%s"><div class="when">%s</div><div class="vs">%s</div>'
                         '<div class="sub">%s</div></a>'
                         % (page_rel(lg, st), esc(lg["name"]), esc(st["name"]), sub))
        parts.append('<div class="lg"><h2>%s</h2><div class="cards">%s</div></div>'
                     % (esc(lg["name"]), "".join(cards)))
    html = HOME_TEMPLATE
    for k, v in (("__CSS__", CSS), ("__NAV__", nav_html("")), ("__BODY__", "\n".join(parts))):
        html = html.replace(k, v)
    return write_page(OUT_HOME, html)


# ----------------------------------------------------------------------------
# ELABORAZIONE PER CAMPIONATO
# ----------------------------------------------------------------------------

def process_league(league, args, now):
    """Scarica e calcola un campionato, genera le sue pagine.
    Ritorna dict con esito; solleva LeagueError su errore fatale."""
    res = {"league": league, "problems": [], "stats": {}, "anomaly": False}
    fail_start = len(FAILED_REQUESTS)

    log("[1/4] indice partite")
    events = fetch_events(league, full=args.full, offline=args.offline)
    if not events:
        raise LeagueError("nessuna partita trovata")

    verify = None
    standings = None
    for stat in implemented_stats():
        log("")
        log("-- %s / %s --" % (league["name"], stat["name"]))
        log("[2/4] %s" % stat["unit"])
        shots = fetch_stats(league, stat, events, full=args.full, offline=args.offline)

        log("[3/4] calcolo")
        played, missing, noscore = build_match_list(events, shots)
        if not played:
            raise LeagueError("nessuna partita con %s disponibili" % stat["unit"])
        teams, tables, series = compute(played)
        ok, msgs = sanity_checks(played, tables)
        for m in msgs:
            log("    " + m)
        if not ok:
            res["anomaly"] = True
            res["problems"].append("controlli interni in anomalia (%s)" % stat["name"])

        verify = find_to_verify(events, noscore, now)
        if verify:
            log("    DATI DA VERIFICARE: %d partite non incluse nelle classifiche" % len(verify))
            for v in verify:
                log("      ! %s %s  %s - %s  [%s]  ESPN: %s  (id %s)  %s" % (
                    v["date"], v["time"], v["home"], v["away"],
                    v["score"] or "senza risultato", v["status"], v["id"], v["reason"]))
        verify_ids = {v["id"] for v in verify}

        # prossime partite: primo gruppo non concluso (le sospette sono "da verificare", non "da giocare")
        upcoming = []
        for ev in events.values():
            if ev["completed"] or ev["id"] in verify_ids:
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
        lag = (now.date() - last_date).days if last_date else None
        banners = []
        if lag is not None and lag > 8:
            banners.append(('warn',
                "Ultima partita nei dati: %s (oltre 8 giorni fa). La fonte potrebbe essere indietro." %
                last["date"]))
        if missing:
            banners.append(('info',
                "%d partite escluse dal calcolo perche' prive di %s." % (len(missing), stat["unit"])))
        nfail = len(FAILED_REQUESTS) - fail_start
        if nfail:
            banners.append(('warn',
                "Aggiornamento parziale: %d richieste a ESPN non riuscite. I dati possono essere indietro." % nfail))

        meta_html = ("Stagione in corso &middot; <b>%d</b> partite incluse &middot; "
                     "ultima: <b>%s</b> (%s) &middot; aggiornato il %s") % (
            len(played), last["date"],
            esc("%s %d-%d %s" % (last["home"], last["gh"], last["ga"], last["away"])),
            dt.datetime.now().strftime("%d/%m/%Y %H:%M"))

        verify_html = ""
        if verify:
            items = "".join(
                "<li><b>%s %s</b> &middot; %s - %s &middot; %s &middot; ESPN: <i>%s</i> &middot; "
                "id %s &middot; %s</li>" % (
                    v["date"], v["time"], esc(v["home"]), esc(v["away"]),
                    ("risultato %s" % esc(v["score"])) if v["score"] else "senza risultato",
                    esc(v["status"]), esc(v["id"]), esc(v["reason"]))
                for v in verify)
            verify_html = ('<div class="banner verify" id="verify-box"><b>Dati da verificare</b> &mdash; %d partite '
                           'con calcio d&rsquo;inizio passato da oltre %d ore ma non concluse per ESPN '
                           '(o senza risultato). Non sono incluse nelle classifiche finch&eacute; non sono concluse.'
                           '<ul>%s</ul></div>' % (len(verify), STALE_HOURS, items))

        missing_html = ""
        if missing:
            rows = "".join(
                '<div class="mrow static"><span class="l">%s</span><span class="c">%s %d-%d %s</span>'
                '<span class="r">%s non disponibili</span></div>' %
                (m["date"], esc(m["home"]), m["gh"], m["ga"], esc(m["away"]), esc(stat["unit"]))
                for m in missing)
            missing_html = '<h2>Dati mancanti</h2><div class="mlist">%s</div>' % rows

        names = {t["id"]: t["name"] for t in teams.values()}
        for u in upcoming:
            names.setdefault(u["home_id"], u["home"])
            names.setdefault(u["away_id"], u["away"])

        data = {
            "stat": {"abbr": stat["abbr"], "unit": stat["unit"], "name": stat["name"]},
            "tables": tables,
            "series": series,
            "names": names,
            "played": [{k: v for k, v in m.items() if k != "dt"} for m in played],
            "upcoming": upcoming,
            "meta_html": meta_html,
            "banners_html": "".join('<div class="banner %s">%s</div>' % (c, esc(t)) for c, t in banners),
            "verify_html": verify_html,
            "missing_html": missing_html,
            "foot_html": ("Fonte: API pubbliche ESPN. Regola: piu' %s = 3 punti, "
                          "pari = 1 punto a testa. Ordinamento a pari punti: differenza %s, "
                          "poi %s fatti. Controlli interni: %s." %
                          (esc(stat["unit"]), esc(stat["unit"]), esc(stat["unit"]), "OK" if ok else "ANOMALIE")),
        }

        log("[4/4] genero la pagina")
        render_page(league, stat, data, len(played))
        pdir = proc_dir(league, stat)
        save_json(os.path.join(pdir, "tables.json"), tables)
        top = tables["total"][0]
        save_json(os.path.join(pdir, "meta.json"), {
            "included": len(played), "excluded_missing": len(missing),
            "to_verify": len(verify), "checks_ok": ok,
            "leader": top["name"], "leader_pti": top["pti"],
            "last_match": "%s %d-%d %s" % (last["home"], last["gh"], last["ga"], last["away"]),
            "last_date": last["date"],
        })
        res["stats"][stat["slug"]] = {
            "included": len(played), "excluded": len(missing), "to_verify": verify,
            "checks_ok": ok, "tables": tables,
        }

    # confronto G con standings ESPN (una volta per campionato, solo online)
    if not args.offline and res["stats"]:
        standings = fetch_standings_games(league)
        any_tables = next(iter(res["stats"].values()))["tables"]
        diff = compare_standings(league, any_tables, standings, verify or [])
        if diff is None:
            log("    standings ESPN non leggibile: confronto G non eseguito")
            res["problems"].append("standings ESPN non leggibile")
        elif diff:
            log("    G vs standings ESPN: %d differenze" % len(diff))
            for d in diff:
                log("      ! " + d)
            res["standings_diff"] = diff
        else:
            log("    G per squadra = standings ESPN (%d squadre): OK" % len(any_tables["total"]))
        res["standings_ok"] = (diff == [])

    nfail = len(FAILED_REQUESTS) - fail_start
    if nfail:
        res["problems"].append("%d richieste ESPN fallite (dati parziali)" % nfail)
    return res


class LeagueError(Exception):
    pass


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def select_leagues(sel):
    if not sel:
        return LEAGUES
    out = [lg for lg in LEAGUES if sel in (lg["code"], lg["slug"])]
    if not out:
        sys.exit("campionato sconosciuto: %s (disponibili: %s)" %
                 (sel, ", ".join("%s/%s" % (l["code"], l["slug"]) for l in LEAGUES)))
    return out


def main():
    ap = argparse.ArgumentParser(description="Classifiche alternative per campionato")
    ap.add_argument("--league", default=None, help="codice ESPN o slug (default: tutti)")
    ap.add_argument("--full", action="store_true", help="riscarica tutta la stagione")
    ap.add_argument("--offline", action="store_true", help="usa solo la cache locale")
    args = ap.parse_args()

    now = utc_now()
    results, failed = [], {}
    for league in select_leagues(args.league):
        log("")
        log("========== %s (%s) ==========" % (league["name"], league["code"]))
        try:
            r = process_league(league, args, now)
            results.append(r)
            if r["problems"]:
                failed[league["slug"]] = "; ".join(r["problems"])
        except LeagueError as e:
            failed[league["slug"]] = str(e)
            log("  !! %s: %s" % (league["name"], e))
        except Exception as e:                          # noqa: BLE001
            failed[league["slug"]] = "errore imprevisto: %r" % (e,)
            log("  !! %s: errore imprevisto: %r" % (league["name"], e))

    # home: sempre rigenerata, con i meta piu' recenti su disco (anche per i campionati falliti)
    metas = {}
    for lg in LEAGUES:
        for st in implemented_stats():
            m = load_json(os.path.join(proc_dir(lg, st), "meta.json"))
            if m:
                metas[(lg["slug"], st["slug"])] = m
    log("")
    log("========== home ==========")
    render_home(metas, set(failed))

    log("")
    log("========== RIEPILOGO ==========")
    for r in results:
        for slug, s in r["stats"].items():
            log("  %-15s %-14s incluse %3d  escluse(senza dati) %d  da verificare %d  controlli %s" % (
                r["league"]["name"], slug, s["included"], s["excluded"], len(s["to_verify"]),
                "OK" if s["checks_ok"] else "ANOMALIE"))
    for slug, why in failed.items():
        log("  !! FALLITO/PARZIALE: %s -> %s" % (slug, why))
    if any(r["anomaly"] for r in results):
        return 2 if not failed else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
