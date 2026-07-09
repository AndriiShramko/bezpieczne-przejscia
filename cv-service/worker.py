# -*- coding: utf-8 -*-
"""Real-time crossing-safety CV service.

Pulls a real public live crossing feed, runs a real Apache-2.0 detector
(YOLOX-s ONNX) on CPU, blurs faces/plates, tracks objects, counts
pedestrians/vehicles, flags potential conflicts (vehicle at the crossing
while a pedestrian is present), snapshots each flagged moment, and serves:

  GET  /live.mjpg      annotated live MJPEG (privacy-blurred)
  GET  /state.json     live counters + recent event feed + human-verified stats
  GET  /snap/<id>.jpg  the annotated screenshot for a flagged event
  POST /api/verify     crowd verification {id, verdict: "confirm"|"refute"}
  GET  /api/stats      aggregate human-verification statistics
  GET  /healthz

Privacy: faces (person head region) and plates (vehicle lower strip) are
pixelated on every frame BEFORE it is shown or saved. Raw frames are never
persisted; only annotated, blurred snapshots of flagged events are stored.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

# HLS/streaming referer for providers that require it (e.g. LanTech). Must be
# set before the first VideoCapture. FFmpeg reads this env at capture time.
_REFERER = os.environ.get("STREAM_REFERER", "")
if _REFERER:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "referer;" + _REFERER

from safecross.blur import blur_regions, head_region
from safecross.detect import OnnxCocoDetector, PERSON, VEHICLE
from safecross.runner import plate_region
from safecross.track import CentroidTracker
from safecross.zones import PolygonZone

# ---------------- config ----------------
VIDEO_URL = os.environ.get("VIDEO_URL", "https://www.youtube.com/watch?v=M3EYAY2MftI")
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "Abbey Road, London — public 24/7 crossing cam")
DENO = os.environ.get("DENO_PATH", "/usr/local/bin/deno")
YTDLP = os.environ.get("YTDLP_BIN", "yt-dlp")
MODEL = os.environ.get("MODEL_PATH", "/app/models/yolox_s.onnx")
DB = os.environ.get("DB_PATH", "/data/events.db")
SNAP_DIR = os.environ.get("SNAP_DIR", "/data/snapshots")
PROC_W = int(os.environ.get("PROC_WIDTH", "1280"))
TARGET_FPS = float(os.environ.get("TARGET_FPS", "3"))
CONF = float(os.environ.get("CONF", "0.35"))
PORT = int(os.environ.get("PORT", "8090"))
MAX_SNAPSHOTS = int(os.environ.get("MAX_SNAPSHOTS", "300"))
EVENT_COOLDOWN = float(os.environ.get("EVENT_COOLDOWN", "12"))

# Crossing polygon as normalized (x,y) fractions — tuned for the default feed,
# override with CROSS_POLY env ("x1,y1;x2,y2;..."). Vehicle+pedestrian both
# present here => potential conflict.
_DEFAULT_POLY = "0.48,0.33;0.74,0.33;0.78,0.47;0.50,0.47"
CROSS_POLY = os.environ.get("CROSS_POLY", _DEFAULT_POLY)

os.makedirs(SNAP_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB), exist_ok=True)


# ---------------- shared state ----------------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = _placeholder_jpeg("Łączenie z kamerą… / connecting…")
        self.live = False
        self.started = time.time()
        self.ped_total = 0
        self.veh_total = 0
        self.event_total = 0
        self.in_ped = 0
        self.in_veh = 0
        self.fps = 0.0
        self.last_frame_ts = 0.0
        self.ticker = deque(maxlen=8)  # short human-readable commentary lines

    def snapshot_counts(self):
        with self.lock:
            elapsed_h = max(1e-6, (time.time() - self.started) / 3600.0)
            return {
                "live": self.live,
                "source": SOURCE_LABEL,
                "uptime_sec": int(time.time() - self.started),
                "ped_total": self.ped_total,
                "veh_total": self.veh_total,
                "event_total": self.event_total,
                "in_frame": {"ped": self.in_ped, "veh": self.in_veh},
                "ped_per_hour": round(self.ped_total / elapsed_h, 1),
                "veh_per_hour": round(self.veh_total / elapsed_h, 1),
                "fps": round(self.fps, 1),
                "ticker": list(self.ticker),
            }


def _placeholder_jpeg(text):
    img = np.full((360, 640, 3), 24, dtype=np.uint8)
    cv2.putText(img, text, (28, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


STATE = State()


# ---------------- db ----------------
def db_conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT, kind TEXT, description TEXT, snapshot TEXT,
        confidence REAL, confirm INTEGER DEFAULT 0, refute INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS votes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER, verdict TEXT, ts_utc TEXT, ip TEXT)""")
    c.commit()
    return c


DBC = db_conn()
DBLOCK = threading.Lock()


def add_event(kind, desc, snap, conf):
    with DBLOCK:
        cur = DBC.execute(
            "INSERT INTO events(ts_utc,kind,description,snapshot,confidence) VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), kind, desc, snap, conf))
        DBC.commit()
        return cur.lastrowid


def recent_events(limit=12):
    with DBLOCK:
        rows = DBC.execute(
            "SELECT id,ts_utc,kind,description,snapshot,confidence,confirm,refute "
            "FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "ts": r[1], "kind": r[2], "desc": r[3], "snap": r[4],
             "confidence": r[5], "confirm": r[6], "refute": r[7]} for r in rows]


def vote(event_id, verdict, ip):
    if verdict not in ("confirm", "refute"):
        return False
    with DBLOCK:
        exists = DBC.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone()
        if not exists:
            return False
        col = "confirm" if verdict == "confirm" else "refute"
        DBC.execute(f"UPDATE events SET {col}={col}+1 WHERE id=?", (event_id,))
        DBC.execute("INSERT INTO votes(event_id,verdict,ts_utc,ip) VALUES(?,?,?,?)",
                    (event_id, verdict, datetime.now(timezone.utc).isoformat(timespec="seconds"), ip))
        DBC.commit()
    return True


def stats():
    with DBLOCK:
        tot = DBC.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        judged = DBC.execute(
            "SELECT COUNT(*) FROM events WHERE confirm+refute>0").fetchone()[0]
        confirmed = DBC.execute(
            "SELECT COUNT(*) FROM events WHERE confirm>refute").fetchone()[0]
        votes_n = DBC.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    precision = round(100.0 * confirmed / judged, 1) if judged else None
    return {"events_total": tot, "events_judged": judged,
            "human_confirmed": confirmed, "votes_total": votes_n,
            "human_precision_pct": precision}


def prune_snapshots():
    try:
        files = sorted(
            (os.path.join(SNAP_DIR, f) for f in os.listdir(SNAP_DIR) if f.endswith(".jpg")),
            key=os.path.getmtime)
        for f in files[:-MAX_SNAPSHOTS]:
            os.remove(f)
    except OSError:
        pass


# ---------------- feed ----------------
# Direct stream (HLS/MJPEG) vs a YouTube page that needs yt-dlp resolution.
IS_DIRECT = (".m3u8" in VIDEO_URL) or (
    VIDEO_URL.startswith("http") and "youtube.com" not in VIDEO_URL
    and "youtu.be" not in VIDEO_URL)


def resolve_manifest():
    if IS_DIRECT:
        return VIDEO_URL  # stable HLS playlist URL, no expiry/bot-gate
    try:
        out = subprocess.run(
            [YTDLP, "--js-runtimes", f"deno:{DENO}", "--no-warnings", "-g", VIDEO_URL],
            capture_output=True, text=True, timeout=60)
        for line in out.stdout.splitlines():
            if line.startswith("http"):
                return line.strip()
    except Exception as e:
        print("resolve_manifest error:", e, flush=True)
    return None


def norm_poly(w, h):
    pts = []
    for pair in CROSS_POLY.split(";"):
        x, y = pair.split(",")
        pts.append((float(x) * w, float(y) * h))
    return pts


# ---------------- annotation ----------------
def draw(frame, dets, ped_tracks, veh_tracks, poly_pts, counts, flagged):
    cv2.polylines(frame, [np.array(poly_pts, np.int32)], True, (60, 200, 255), 2)
    for d in dets:
        x1, y1, x2, y2 = [int(v) for v in d.xyxy]
        c = (90, 230, 120) if d.cls == PERSON else (80, 150, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
    banner = frame[:44].copy()
    frame[:44] = (frame[:44] * 0.35).astype(np.uint8)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cv2.circle(frame, (20, 22), 7, (60, 60, 235), -1)
    cv2.putText(frame, f"LIVE  |  piesi w kadrze: {counts[0]}  pojazdy: {counts[1]}"
                       f"  |  {ts}", (36, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
    if flagged:
        cv2.rectangle(frame, (0, 0), (frame.shape[1] - 1, frame.shape[0] - 1),
                      (0, 0, 235), 6)
        cv2.putText(frame, "POTENCJALNY KONFLIKT / POTENTIAL CONFLICT",
                    (36, frame.shape[0] - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 235), 2, cv2.LINE_AA)
    return frame


# ---------------- worker loop ----------------
def worker():
    det = OnnxCocoDetector(MODEL, conf_thres=CONF)
    ped_tr = CentroidTracker(max_dist=90, ttl=8)
    veh_tr = CentroidTracker(max_dist=120, ttl=8)
    seen_ped, seen_veh = set(), set()
    poly = None
    zone = None
    last_event = 0.0
    manifest = None
    manifest_ts = 0.0
    frame_interval = 1.0 / TARGET_FPS
    tprev = time.time()

    while True:
        # direct HLS URLs are stable; only re-resolve YouTube manifests (expire)
        stale = (not IS_DIRECT) and (time.time() - manifest_ts) > 1800
        if manifest is None or stale:
            manifest = resolve_manifest()
            manifest_ts = time.time()
            if not manifest:
                STATE.live = False
                STATE.jpeg = _placeholder_jpeg("Brak sygnału — ponawiam / no signal, retrying")
                time.sleep(10)
                continue
        cap = cv2.VideoCapture(manifest, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            manifest = None
            time.sleep(5)
            continue
        fail = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                fail += 1
                if fail > 30:
                    break
                time.sleep(0.2)
                continue
            fail = 0
            now = time.time()
            if now - tprev < frame_interval:
                continue
            dt = now - tprev
            tprev = now

            h0, w0 = frame.shape[:2]
            if w0 > PROC_W:
                frame = cv2.resize(frame, (PROC_W, int(h0 * PROC_W / w0)))
            h, w = frame.shape[:2]
            if poly is None:
                poly = norm_poly(w, h)
                zone = PolygonZone(poly)

            dets = det.detect(frame)
            # privacy: pixelate faces + plates BEFORE anything else
            pboxes = [head_region(d.xyxy) for d in dets if d.cls == PERSON]
            pboxes += [plate_region(d.xyxy) for d in dets if d.cls == VEHICLE]
            frame = blur_regions(frame, pboxes, block=10)

            peds = [d for d in dets if d.cls == PERSON]
            vehs = [d for d in dets if d.cls == VEHICLE]
            ped_tracks = ped_tr.update(peds)
            veh_tracks = veh_tr.update(vehs)
            new_ped = len(set(ped_tracks) - seen_ped)
            new_veh = len(set(veh_tracks) - seen_veh)
            seen_ped |= set(ped_tracks)
            seen_veh |= set(veh_tracks)

            ped_in = [p for p in ped_tracks.values() if zone.contains(p)]
            veh_in = [v for v in veh_tracks.values() if zone.contains(v)]
            flagged = bool(ped_in) and bool(veh_in)

            with STATE.lock:
                STATE.ped_total += new_ped
                STATE.veh_total += new_veh
                STATE.in_ped = len(peds)
                STATE.in_veh = len(vehs)
                STATE.fps = 0.8 * STATE.fps + 0.2 * (1.0 / dt if dt > 0 else 0)
                STATE.live = True
                STATE.last_frame_ts = now

            annotated = draw(frame.copy(), dets, ped_tracks, veh_tracks, poly,
                             (len(peds), len(vehs)), flagged)

            if flagged and (now - last_event) > EVENT_COOLDOWN:
                last_event = now
                conf = float(np.mean([d.conf for d in dets])) if dets else 0.0
                snapname = f"ev_{int(now)}.jpg"
                cv2.imwrite(os.path.join(SNAP_DIR, snapname), annotated,
                            [cv2.IMWRITE_JPEG_QUALITY, 80])
                desc = (f"Pojazd i pieszy jednocześnie w strefie przejścia "
                        f"(piesi: {len(ped_in)}, pojazdy: {len(veh_in)}). "
                        f"Sprawdź, czy kierowca ustąpił pierwszeństwa.")
                eid = add_event("potential_conflict", desc, snapname, round(conf, 2))
                with STATE.lock:
                    STATE.event_total += 1
                    STATE.ticker.appendleft(
                        f"#{eid} potencjalny konflikt: pojazd przy przejściu z pieszym")
                prune_snapshots()
            elif new_ped and int(now) % 5 == 0:
                with STATE.lock:
                    STATE.ticker.appendleft(
                        f"+{new_ped} pieszych, +{new_veh} pojazdów w kadrze")

            ok2, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 72])
            if ok2:
                with STATE.lock:
                    STATE.jpeg = buf.tobytes()
        cap.release()
        STATE.live = False
        manifest = None
        time.sleep(2)


# ---------------- http ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self._send(200, json.dumps({"ok": True, "live": STATE.live}))
        if path == "/state.json":
            data = STATE.snapshot_counts()
            data["events"] = recent_events(12)
            data["stats"] = stats()
            return self._send(200, json.dumps(data))
        if path == "/api/stats":
            return self._send(200, json.dumps(stats()))
        if path.startswith("/snap/"):
            name = os.path.basename(path[len("/snap/"):])
            fp = os.path.join(SNAP_DIR, name)
            if os.path.isfile(fp) and name.endswith(".jpg"):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            return self._send(404, json.dumps({"ok": False}))
        if path == "/live.mjpg":
            return self._stream_mjpeg()
        return self._send(404, json.dumps({"ok": False}))

    def do_POST(self):
        if self.path.split("?")[0] != "/api/verify":
            return self._send(404, json.dumps({"ok": False}))
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 4096)
            data = json.loads(self.rfile.read(n))
            ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
            ok = vote(int(data.get("id")), str(data.get("verdict")), ip)
            return self._send(200 if ok else 400, json.dumps({"ok": ok}))
        except Exception:
            return self._send(400, json.dumps({"ok": False}))

    def _stream_mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                with STATE.lock:
                    buf = STATE.jpeg
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                self.wfile.write(buf)
                self.wfile.write(b"\r\n")
                time.sleep(1.0 / max(1.0, TARGET_FPS))
        except (BrokenPipeError, ConnectionError, OSError):
            return


def main():
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"cv-service on :{PORT} feed={VIDEO_URL}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
