# -*- coding: utf-8 -*-
"""SQLite layer v2: events with clips + AI verdicts, votes, hourly stats,
speed samples, camera health, AI usage cap. Single connection, one lock."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "/data/events.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_LOCK = threading.Lock()
_C = sqlite3.connect(DB_PATH, check_same_thread=False)
_C.execute("PRAGMA journal_mode=WAL")
_C.executescript("""
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT, cam_id TEXT, kind TEXT DEFAULT 'potential_conflict',
  description TEXT, snapshot TEXT, clip TEXT, duration_s REAL,
  tl_state TEXT, max_veh_kmh REAL, n_ped INTEGER, n_veh INTEGER,
  status TEXT DEFAULT 'pending_ai',           -- pending_ai | ai_done | ai_skipped
  ai_verdict TEXT,                            -- violation | no_violation | uncertain
  ai_violator TEXT, ai_explanation_pl TEXT, ai_explanation_en TEXT,
  ai_confidence REAL,
  confirm INTEGER DEFAULT 0, refute INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS votes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER, verdict TEXT, ts_utc TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS hourly(
  cam_id TEXT, hour_utc TEXT, ped INTEGER DEFAULT 0, veh INTEGER DEFAULT 0,
  events INTEGER DEFAULT 0, violations_ai INTEGER DEFAULT 0,
  speed_sum REAL DEFAULT 0, speed_n INTEGER DEFAULT 0, max_kmh REAL DEFAULT 0,
  observed_s REAL DEFAULT 0,
  PRIMARY KEY (cam_id, hour_utc));
CREATE TABLE IF NOT EXISTS speeds(
  ts_utc TEXT, cam_id TEXT, kind TEXT, kmh REAL);
CREATE TABLE IF NOT EXISTS camera_health(
  ts_utc TEXT, cam_id TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS ai_usage(
  day TEXT PRIMARY KEY, calls INTEGER DEFAULT 0);
""")
_C.commit()

# migrations for DBs created before the bike counter existed
try:
    _C.execute("ALTER TABLE hourly ADD COLUMN bike INTEGER DEFAULT 0")
    _C.commit()
except sqlite3.OperationalError:
    pass  # column already there


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hour():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def add_event(cam_id, description, snapshot, clip, duration_s, tl_state,
              max_veh_kmh, n_ped, n_veh):
    with _LOCK:
        cur = _C.execute(
            "INSERT INTO events(ts_utc,cam_id,description,snapshot,clip,duration_s,"
            "tl_state,max_veh_kmh,n_ped,n_veh) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_now(), cam_id, description, snapshot, clip, round(duration_s, 1),
             tl_state, max_veh_kmh, n_ped, n_veh))
        _C.execute("INSERT INTO hourly(cam_id,hour_utc,events) VALUES(?,?,1) "
                   "ON CONFLICT(cam_id,hour_utc) DO UPDATE SET events=events+1",
                   (cam_id, _hour()))
        _C.commit()
        return cur.lastrowid


def set_ai_result(eid, verdict, violator, expl_pl, expl_en, conf):
    with _LOCK:
        _C.execute("UPDATE events SET status='ai_done',ai_verdict=?,ai_violator=?,"
                   "ai_explanation_pl=?,ai_explanation_en=?,ai_confidence=? WHERE id=?",
                   (verdict, violator, expl_pl, expl_en, conf, eid))
        if verdict == "violation":
            row = _C.execute("SELECT cam_id FROM events WHERE id=?", (eid,)).fetchone()
            if row:
                _C.execute("INSERT INTO hourly(cam_id,hour_utc,violations_ai) VALUES(?,?,1) "
                           "ON CONFLICT(cam_id,hour_utc) DO UPDATE SET violations_ai=violations_ai+1",
                           (row[0], _hour()))
        _C.commit()


def set_ai_skipped(eid):
    with _LOCK:
        _C.execute("UPDATE events SET status='ai_skipped' WHERE id=?", (eid,))
        _C.commit()


def next_pending_ai():
    with _LOCK:
        r = _C.execute("SELECT id,cam_id,clip,tl_state,max_veh_kmh,n_ped,n_veh "
                       "FROM events WHERE status='pending_ai' ORDER BY id LIMIT 1").fetchone()
    return r


def list_events(tab="all", limit=12, offset=0, cam_id=None):
    q = ("SELECT id,ts_utc,cam_id,description,snapshot,clip,duration_s,tl_state,"
         "max_veh_kmh,status,ai_verdict,ai_explanation_pl,ai_explanation_en,"
         "ai_confidence,confirm,refute FROM events")
    where, args = [], []
    if tab == "violation":
        where.append("ai_verdict='violation'")
    elif tab == "rejected":
        where.append("ai_verdict IN ('no_violation','uncertain')")
    elif tab == "pending":
        where.append("status IN ('pending_ai','ai_skipped') AND ai_verdict IS NULL")
    if cam_id:
        where.append("cam_id=?"); args.append(cam_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _LOCK:
        rows = _C.execute(q, args).fetchall()
    keys = ["id", "ts", "cam", "desc", "snap", "clip", "dur", "tl", "kmh", "status",
            "ai_verdict", "ai_pl", "ai_en", "ai_conf", "confirm", "refute"]
    return [dict(zip(keys, r)) for r in rows]


def vote(event_id, verdict, ip):
    if verdict not in ("violation", "false_alarm"):
        return False
    col = "confirm" if verdict == "violation" else "refute"
    with _LOCK:
        if not _C.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
            return False
        _C.execute(f"UPDATE events SET {col}={col}+1 WHERE id=?", (event_id,))
        _C.execute("INSERT INTO votes(event_id,verdict,ts_utc,ip) VALUES(?,?,?,?)",
                   (event_id, verdict, _now(), ip))
        _C.commit()
    return True


def bump_counts(cam_id, ped=0, veh=0, observed_s=0.0, bike=0):
    with _LOCK:
        _C.execute(
            "INSERT INTO hourly(cam_id,hour_utc,ped,veh,observed_s,bike) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(cam_id,hour_utc) DO UPDATE SET ped=ped+excluded.ped,"
            "veh=veh+excluded.veh,observed_s=observed_s+excluded.observed_s,"
            "bike=bike+excluded.bike",
            (cam_id, _hour(), ped, veh, observed_s, bike))
        _C.commit()


def add_speed(cam_id, kind, kmh):
    with _LOCK:
        _C.execute("INSERT INTO speeds(ts_utc,cam_id,kind,kmh) VALUES(?,?,?,?)",
                   (_now(), cam_id, kind, round(kmh, 1)))
        _C.execute(
            "INSERT INTO hourly(cam_id,hour_utc,speed_sum,speed_n,max_kmh) VALUES(?,?,?,1,?) "
            "ON CONFLICT(cam_id,hour_utc) DO UPDATE SET speed_sum=speed_sum+excluded.speed_sum,"
            "speed_n=speed_n+1, max_kmh=MAX(max_kmh,excluded.max_kmh)",
            (cam_id, _hour(), kmh, kmh))
        # prune: keep ~20k samples
        _C.execute("DELETE FROM speeds WHERE rowid IN (SELECT rowid FROM speeds "
                   "ORDER BY rowid DESC LIMIT -1 OFFSET 20000)")
        _C.commit()


def health(cam_id, event, detail=""):
    with _LOCK:
        _C.execute("INSERT INTO camera_health(ts_utc,cam_id,event,detail) VALUES(?,?,?,?)",
                   (_now(), cam_id, event, detail))
        _C.commit()


def camera_uptime_stats():
    with _LOCK:
        rows = _C.execute(
            "SELECT cam_id, event, COUNT(*), MAX(ts_utc) FROM camera_health "
            "GROUP BY cam_id, event ORDER BY cam_id").fetchall()
    out = {}
    for cam, ev, n, last in rows:
        out.setdefault(cam, {})[ev] = {"count": n, "last": last}
    return out


def stats(cam_id=None):
    w, args = ("WHERE cam_id=?", [cam_id]) if cam_id else ("", [])
    with _LOCK:
        tot = _C.execute(f"SELECT COUNT(*) FROM events {w}", args).fetchone()[0]
        ai_done = _C.execute(
            f"SELECT COUNT(*) FROM events {w}{' AND' if w else ' WHERE'} status='ai_done'",
            args).fetchone()[0]
        ai_viol = _C.execute(
            f"SELECT COUNT(*) FROM events {w}{' AND' if w else ' WHERE'} ai_verdict='violation'",
            args).fetchone()[0]
        judged = _C.execute(
            f"SELECT COUNT(*) FROM events {w}{' AND' if w else ' WHERE'} confirm+refute>0",
            args).fetchone()[0]
        human_viol = _C.execute(
            f"SELECT COUNT(*) FROM events {w}{' AND' if w else ' WHERE'} confirm>refute",
            args).fetchone()[0]
        agree = _C.execute(
            f"SELECT COUNT(*) FROM events {w}{' AND' if w else ' WHERE'} confirm+refute>0 "
            "AND ((ai_verdict='violation' AND confirm>refute) OR "
            "(ai_verdict IN ('no_violation','uncertain') AND refute>=confirm))",
            args).fetchone()[0]
        votes_n = _C.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    return {"events_total": tot, "ai_analyzed": ai_done, "ai_violations": ai_viol,
            "human_judged": judged, "human_violations": human_viol,
            "ai_human_agreement_pct": round(100.0 * agree / judged, 1) if judged else None,
            "votes_total": votes_n}


def charts(cam_id, hours=48):
    with _LOCK:
        rows = _C.execute(
            "SELECT hour_utc,ped,veh,events,violations_ai,speed_sum,speed_n,max_kmh,observed_s,"
            "bike FROM hourly WHERE cam_id=? ORDER BY hour_utc DESC LIMIT ?",
            (cam_id, hours)).fetchall()
        sp = [r[0] for r in _C.execute(
            "SELECT kmh FROM speeds WHERE cam_id=? AND kind='vehicle' "
            "ORDER BY rowid DESC LIMIT 3000", (cam_id,)).fetchall()]
    hourly = [{"h": r[0], "ped": r[1], "veh": r[2], "ev": r[3], "viol": r[4],
               "avg_kmh": round(r[5] / r[6], 1) if r[6] else None,
               "max_kmh": r[7], "obs_s": round(r[8]), "bike": r[9] or 0}
              for r in reversed(rows)]
    hist = [0] * 14  # 0-5,5-10,...,65-70+
    clean = []
    for v in sp:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        clean.append(fv)
        hist[min(13, max(0, int(fv // 5)))] += 1
    sp = clean
    return {"hourly": hourly, "speed_hist_bins_kmh5": hist, "speed_n": len(sp)}


def ai_calls_today():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _LOCK:
        r = _C.execute("SELECT calls FROM ai_usage WHERE day=?", (day,)).fetchone()
    return r[0] if r else 0


def ai_call_inc():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _LOCK:
        _C.execute("INSERT INTO ai_usage(day,calls) VALUES(?,1) "
                   "ON CONFLICT(day) DO UPDATE SET calls=calls+1", (day,))
        _C.commit()


def all_events_for_report(cam_id, limit=500):
    with _LOCK:
        rows = _C.execute(
            "SELECT id,ts_utc,duration_s,tl_state,max_veh_kmh,status,ai_verdict,"
            "ai_explanation_pl,ai_confidence,confirm,refute FROM events WHERE cam_id=? "
            "ORDER BY id DESC LIMIT ?", (cam_id, limit)).fetchall()
    return rows
