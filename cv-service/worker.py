# -*- coding: utf-8 -*-
"""Bezpieczne Przejścia — real-time crossing-safety CV service v2.

Cooperating multi-model system:
  YOLOX (local, per frame)  ->  episode detector (zone topology + MOTION gate)
  -> low-res episode CLIP (pre/post roll)  ->  Gemini Flash-Lite verdict+explanation
  -> human verification on the site  ->  stats/agreement back into the DB.

Key v2 behaviours:
- An episode = ONE event (state machine), no snapshot series; a waiting
  (stationary) car is NOT an event — vehicles must be MOVING in the zone.
- Every episode is recorded as a small MP4 (blurred, annotated, low fps/res).
- Traffic-light colour is sampled from scene-context bboxes (HSV) and stored.
- Vehicle/pedestrian speed estimates (km/h, rough monocular scale) per track.
- Camera registry with admin API + automatic failover, per-camera stats.
- Disk guard: prunes clips, stops recording below limits, Telegram alert.

Privacy: faces/plates pixelated BEFORE display/recording; only counters,
blurred clips/snapshots and anonymous votes are persisted.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

import ai_analyst
import db
from safecross.blur import blur_regions, head_region
from safecross.detect import Detection, OnnxCocoDetector, PERSON, VEHICLE
from safecross.runner import plate_region
from safecross.track import CentroidTracker
from safecross.zones import PolygonZone

# ---------------- config ----------------
DATA = os.environ.get("DATA_DIR", "/data")
MODEL = os.environ.get("MODEL_PATH", "/app/models/yolox_s.onnx")
PORT = int(os.environ.get("PORT", "8090"))
PROC_W = int(os.environ.get("PROC_WIDTH", "1280"))
TARGET_FPS = float(os.environ.get("TARGET_FPS", "2"))
CONF = float(os.environ.get("CONF", "0.28"))
CLIP_W = int(os.environ.get("CLIP_WIDTH", "640"))
CLIP_FPS = float(os.environ.get("CLIP_FPS", "2"))
PRE_ROLL_S = float(os.environ.get("PRE_ROLL_S", "6"))
POST_ROLL_S = float(os.environ.get("POST_ROLL_S", "3"))
EPISODE_END_S = float(os.environ.get("EPISODE_END_S", "4"))
EPISODE_MAX_S = float(os.environ.get("EPISODE_MAX_S", "35"))
MOVE_KMH_MIN = float(os.environ.get("MOVE_KMH_MIN", "4"))
FG_MIN = float(os.environ.get("FG_MIN", "0.05"))        # foreground fraction => "moving now"
MIN_TRACK_FRAMES = int(os.environ.get("MIN_TRACK_FRAMES", "4"))
MIN_MOVE_PX = float(os.environ.get("MIN_MOVE_PX", "16"))  # on PROC-width frame
MIN_EVENT_FRAMES = int(os.environ.get("MIN_EVENT_FRAMES", "3"))
MIN_CLIP_SEC = float(os.environ.get("MIN_CLIP_SEC", "3"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CLIPS_MAX_GB = float(os.environ.get("CLIPS_MAX_GB", "2.0"))
DISK_MIN_FREE_GB = float(os.environ.get("DISK_MIN_FREE_GB", "2.0"))
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")

SNAP_DIR = os.path.join(DATA, "snapshots")
CLIP_DIR = os.path.join(DATA, "clips")
SCENE_DIR = os.path.join(DATA, "scenes")
for d in (SNAP_DIR, CLIP_DIR, SCENE_DIR):
    os.makedirs(d, exist_ok=True)

_REFERER_DEFAULT = os.environ.get("STREAM_REFERER", "")


# ---------------- camera registry ----------------
CAMS_FILE = os.path.join(DATA, "cameras.json")
_CAMS_LOCK = threading.Lock()


def _default_cams():
    return {
        "active": "sch",
        "cameras": [{
            "id": "sch",
            "label": "Skrzyżowanie CH Słoneczne, Szczecin — publiczna kamera (LanTech LiveSzczecin)",
            "url": os.environ.get("VIDEO_URL",
                                  "https://ls-proxy-local.lantech.com.pl/hls/sch/index.m3u8"),
            "referer": _REFERER_DEFAULT or "https://lantech.com.pl/",
            "poly": [[0.44, 0.62], [0.77, 0.60], [0.80, 0.80], [0.47, 0.84]],
            "m_per_px_fullw": float(os.environ.get("M_PER_PX", "0.075")),
        }],
    }


def cams_load():
    with _CAMS_LOCK:
        if not os.path.exists(CAMS_FILE):
            cams_save(_default_cams())
        with open(CAMS_FILE, encoding="utf-8") as f:
            return json.load(f)


def cams_save(cfg):
    tmp = CAMS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CAMS_FILE)


# ---------------- shared state ----------------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = _placeholder("Łączenie z kamerą… / connecting…")
        self.live = False
        self.cam_id = ""
        self.cam_label = ""
        self.started = time.time()
        self.ped_total = 0
        self.veh_total = 0
        self.in_ped = 0
        self.in_veh = 0
        self.fps = 0.0
        self.tl = {}
        self.speeds_now = {"veh_kmh": None, "ped_kmh": None}
        self.ticker = deque(maxlen=8)
        self.recording_ok = True
        self.episode_active = False


def _placeholder(text):
    img = np.full((360, 640, 3), 18, dtype=np.uint8)
    cv2.putText(img, text, (24, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 1)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


S = State()


# ---------------- traffic lights (HSV in scene bboxes) ----------------
def tl_color(frame, bbox_norm):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox_norm
    x1, x2 = int(min(x1, x2) * w), int(max(x1, x2) * w)
    y1, y2 = int(min(y1, y2) * h), int(max(y1, y2) * h)
    x2 = max(x2, x1 + 2); y2 = max(y2, y1 + 2)
    roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if roi.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = (vv > 140) & (ss > 90)
    red = int((bright & ((hh < 12) | (hh > 168))).sum())
    green = int((bright & (hh > 40) & (hh < 95)).sum())
    amber = int((bright & (hh >= 12) & (hh <= 35)).sum())
    m = max(red, green, amber)
    if m < 4:
        return "unknown"
    return "red" if m == red else ("green" if m == green else "amber")


# ---------------- speed estimation ----------------
class SpeedBook:
    """Rough monocular speed per track + a motion classifier.

    A track is 'stationary' only when we have enough evidence: a long-enough
    history (>=STAT_SEC) with tiny total displacement. A short/new track is NOT
    stationary (a car quickly crossing the zebra has few samples) — so we never
    exclude a real crosser, but we DO exclude a car waiting at a red light.
    """
    STAT_SEC = 2.2

    def __init__(self, m_per_px):
        self.m_per_px = m_per_px
        self.hist = {}  # tid -> deque[(t, x, y)]

    def update(self, tracks, now):
        out = {}
        self.stationary = set()
        stat_px = max(6.0, (MOVE_KMH_MIN / 3.6) / max(1e-6, self.m_per_px) * self.STAT_SEC)
        for tid, (x, y) in tracks.items():
            q = self.hist.setdefault(tid, deque(maxlen=16))
            q.append((now, x, y))
            if len(q) >= 3 and q[-1][0] - q[0][0] >= 0.8:
                t0, x0, y0 = q[0]
                dt = now - t0
                d_px = float(((x - x0) ** 2 + (y - y0) ** 2) ** 0.5)
                out[tid] = float((d_px / dt) * self.m_per_px * 3.6)  # km/h approx
            span = q[-1][0] - q[0][0]
            if span >= self.STAT_SEC:
                xs = [p[1] for p in q]; ys = [p[2] for p in q]
                disp = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
                if disp < stat_px:
                    self.stationary.add(tid)
        for tid in list(self.hist):
            if tid not in tracks:
                del self.hist[tid]
        return out

    def is_moving(self, tid):
        return tid not in getattr(self, "stationary", set())


class ConfirmBook:
    """Confirms a track is a REAL object only after it has been tracked for
    >=min_frames AND has actually MOVED >=min_move_px over its lifetime. This
    is what kills static false positives (a pole/sign/traffic-light misread as
    person/car never moves, so it is never confirmed and never counted) and
    single-frame phantom flickers."""

    def __init__(self, min_frames, min_move_px):
        self.min_frames = min_frames
        self.min_move = min_move_px
        self.info = {}
        self.newly = set()

    def update(self, tracks, fg_hits):
        self.newly = set()
        for tid, (x, y) in tracks.items():
            e = self.info.get(tid)
            if e is None:
                e = {"n": 0, "x0": x, "y0": y, "maxd": 0.0, "ok": False, "fg": 0}
                self.info[tid] = e
            e["n"] += 1
            d = ((x - e["x0"]) ** 2 + (y - e["y0"]) ** 2) ** 0.5
            if d > e["maxd"]:
                e["maxd"] = d
            if tid in fg_hits:
                e["fg"] += 1
            if not e["ok"] and e["n"] >= self.min_frames and e["maxd"] >= self.min_move:
                e["ok"] = True
                self.newly.add(tid)
        for tid in list(self.info):
            if tid not in tracks:
                del self.info[tid]
        return self.newly

    def confirmed(self, tid):
        e = self.info.get(tid)
        return bool(e and e["ok"])

    def active(self, tid):
        """Confirmed (has moved) OR currently showing foreground — used to
        decide what to DRAW, so we never box a static pole."""
        e = self.info.get(tid)
        return bool(e and (e["ok"] or e["fg"] > 0))


def fg_ratio_at(fgmask, x, y, r=22):
    h, w = fgmask.shape[:2]
    x1, y1 = max(0, int(x - r)), max(0, int(y - r))
    x2, y2 = min(w, int(x + r)), min(h, int(y + r))
    patch = fgmask[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0
    return float((patch > 0).mean())


# ---------------- episode recorder ----------------
class Episode:
    def __init__(self):
        self.frames = []          # (ts, small_bgr)
        self.start = 0.0
        self.last_cond = 0.0
        self.max_kmh = 0.0
        self.n_ped = 0
        self.n_veh = 0
        self.tl_states = []
        self.best_snap = None
        self.best_overlap = -1


def write_clip(path, frames, fps):
    if not frames:
        return False
    h, w = frames[0][1].shape[:2]
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _, f in frames:
        vw.write(f)
    vw.release()
    return os.path.getsize(path) > 0


# ---------------- helpers ----------------
def _iou(a, b):
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def poly_bbox(poly, w, h, margin=0.10):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    mx = (max(xs) - min(xs)) * margin; my = (max(ys) - min(ys)) * margin
    return (max(0, int(min(xs) - mx)), max(0, int(min(ys) - my)),
            min(w, int(max(xs) + mx)), min(h, int(max(ys) + my)))


def detect_roi(det, frame, bbox, up_to=900):
    x1, y1, x2, y2 = bbox
    crop = frame[y1:y2, x1:x2]
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return []
    scale = min(2.5, max(1.0, up_to / max(1, crop.shape[1])))
    if scale > 1.01:
        crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
    out = []
    for d in det.detect(crop):
        bx1, by1, bx2, by2 = d.xyxy
        out.append(Detection((bx1 / scale + x1, by1 / scale + y1,
                              bx2 / scale + x1, by2 / scale + y1), d.cls, d.conf))
    return out


def _scene_ignore(scene, w, h):
    """Pixel boxes of known STATIC scene objects (traffic lights, signs, poles,
    statues, lit shop signs) that the detector must NOT treat as car/person."""
    if not scene:
        return []
    out = []
    for t in scene.get("traffic_lights", []):
        b = t.get("bbox", [])
        if len(b) == 4:
            out.append((b[0] * w, b[1] * h, b[2] * w, b[3] * h))
    for r in scene.get("ignore_regions", []):
        b = r.get("bbox", r) if isinstance(r, dict) else r
        if isinstance(b, (list, tuple)) and len(b) == 4:
            out.append((b[0] * w, b[1] * h, b[2] * w, b[3] * h))
    # pad tiny light boxes so a nearby misdetection still overlaps
    padded = []
    for x1, y1, x2, y2 in out:
        pw, ph = (x2 - x1) * 0.6 + 8, (y2 - y1) * 0.6 + 8
        padded.append((x1 - pw, y1 - ph, x2 + pw, y2 + ph))
    return padded


def scene_path(cam_id):
    return os.path.join(SCENE_DIR, f"scene_{cam_id}.json")


def load_scene(cam_id):
    p = scene_path(cam_id)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def norm_scene_coords(scene):
    """Defensive: model sometimes mixes pixels and fractions, or returns
    malformed points. Skip anything that isn't a clean [x,y] / [x1,y1,x2,y2]."""
    def nrm(v, size=1920):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return v / size if v > 1.5 else v
    for c in scene.get("crossings", []):
        pts = []
        for p in c.get("polygon", []):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append([nrm(p[0]), nrm(p[1], 1080)])
        c["polygon"] = pts
    for t in scene.get("traffic_lights", []):
        b = t.get("bbox", [])
        if isinstance(b, (list, tuple)) and len(b) == 4:
            t["bbox"] = [nrm(b[0]), nrm(b[1], 1080), nrm(b[2]), nrm(b[3], 1080)]
    for r in scene.get("ignore_regions", []):
        b = r.get("bbox") if isinstance(r, dict) else r
        if isinstance(b, (list, tuple)) and len(b) == 4:
            nb = [nrm(b[0]), nrm(b[1], 1080), nrm(b[2]), nrm(b[3], 1080)]
            if isinstance(r, dict):
                r["bbox"] = nb
    return scene


def tg_alert(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ---------------- disk guard ----------------
_disk = {"alerted": 0.0}


def disk_guard():
    while True:
        try:
            free_gb = shutil.disk_usage(DATA).free / 1e9
            clips = sorted((os.path.join(CLIP_DIR, f) for f in os.listdir(CLIP_DIR)),
                           key=os.path.getmtime)
            total = sum(os.path.getsize(c) for c in clips) / 1e9
            while clips and (total > CLIPS_MAX_GB or free_gb < DISK_MIN_FREE_GB + 0.5):
                c = clips.pop(0)
                total -= os.path.getsize(c) / 1e9
                os.remove(c)
            snaps = sorted((os.path.join(SNAP_DIR, f) for f in os.listdir(SNAP_DIR)),
                           key=os.path.getmtime)
            for sfile in snaps[:-400]:
                os.remove(sfile)
            S.recording_ok = free_gb > DISK_MIN_FREE_GB
            if free_gb < DISK_MIN_FREE_GB and time.time() - _disk["alerted"] > 6 * 3600:
                _disk["alerted"] = time.time()
                tg_alert(f"[patrol] ⚠️ disk low: {free_gb:.1f} GB free — clip recording paused, "
                         f"pruning old clips (patrol-cv disk guard)")
        except Exception as e:
            print("disk_guard:", e, flush=True)
        time.sleep(60)


# ---------------- AI analyzer thread ----------------
def analyzer_loop():
    while True:
        row = db.next_pending_ai()
        if not row:
            time.sleep(5)
            continue
        eid, cam_id, clip, tl_state, kmh, n_ped, n_veh = row
        path = os.path.join(CLIP_DIR, clip or "")
        if not clip or not os.path.exists(path):
            db.set_ai_skipped(eid)
            continue
        if not ai_analyst.enabled():
            time.sleep(30)
            continue
        try:
            cap = cv2.VideoCapture(path)
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
            picks = sorted({max(0, min(n - 1, int(i))) for i in
                            np.linspace(0, n - 1, num=min(8, n))})
            frames = []
            for idx in picks:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, fr = cap.read()
                if ok:
                    ok2, jb = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok2:
                        frames.append(jb.tobytes())
            cap.release()
            scene = load_scene(cam_id)
            res = ai_analyst.analyze_event(frames, scene, tl_state, kmh, n_ped, n_veh,
                                           fps=CLIP_FPS)
            if res:
                db.set_ai_result(eid, res["verdict"], res.get("violator", "none"),
                                 res.get("explanation_pl", ""), res.get("explanation_en", ""),
                                 float(res.get("confidence", 0)))
                with S.lock:
                    S.ticker.appendleft(
                        f"AI #{eid}: {'NARUSZENIE' if res['verdict']=='violation' else ('OK/fałszywy alarm' if res['verdict']=='no_violation' else 'niepewne')}")
                print(f"AI verdict #{eid}: {res['verdict']}", flush=True)
            else:
                db.set_ai_skipped(eid)
        except Exception as e:
            print("analyzer:", e, flush=True)
            db.set_ai_skipped(eid)


# ---------------- main processing loop ----------------
def worker():
    det = OnnxCocoDetector(MODEL, conf_thres=CONF)
    frame_interval = 1.0 / TARGET_FPS
    fail_start = None

    while True:
        cfg = cams_load()
        by_id = {c["id"]: c for c in cfg["cameras"]}
        order = [cfg.get("active")] + [c["id"] for c in cfg["cameras"]
                                       if c["id"] != cfg.get("active")]
        cam = None
        for cid in order:
            if cid in by_id:
                cam = by_id[cid]
                if _open_and_run(det, cam, frame_interval, by_id, cfg):
                    break  # config changed / stream ended cleanly -> reload config
                db.health(cid, "down", "stream failed")
        if cam is None:
            time.sleep(5)
        S.live = False
        time.sleep(3)


def _open_and_run(det, cam, frame_interval, by_id, cfg):
    """Run one camera until failure (False) or admin switched active (True)."""
    cam_id = cam["id"]
    if cam.get("referer"):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "referer;" + cam["referer"]
    cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False
    db.health(cam_id, "up", "stream opened")
    with S.lock:
        S.cam_id, S.cam_label, S.live = cam_id, cam.get("label", cam_id), True
        S.ped_total = S.veh_total = 0
        S.started = time.time()

    ped_tr = CentroidTracker(max_dist=90, ttl=8)
    veh_tr = CentroidTracker(max_dist=130, ttl=8)
    ped_cb = ConfirmBook(MIN_TRACK_FRAMES, MIN_MOVE_PX)
    veh_cb = ConfirmBook(MIN_TRACK_FRAMES, MIN_MOVE_PX)
    bgsub = cv2.createBackgroundSubtractorMOG2(history=400, varThreshold=40,
                                               detectShadows=False)
    cond_streak = 0
    poly = None; zone = None; bbox = None
    scene = load_scene(cam_id)
    if scene:
        scene = norm_scene_coords(scene)
    m_per_px = None
    speeds = None
    ring = deque()  # (ts, clip_frame)
    ep = None
    scene_try_ts = 0.0
    tprev = time.time()
    fails = 0
    last_cfg_check = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            fails += 1
            if fails > 40:
                cap.release()
                return False
            time.sleep(0.15)
            continue
        fails = 0
        now = time.time()
        if now - tprev < frame_interval:
            continue
        dt = now - tprev
        tprev = now

        # admin may have switched camera / edited config
        if now - last_cfg_check > 5:
            last_cfg_check = now
            c2 = cams_load()
            if c2.get("active") != cam_id or c2["cameras"] != cfg["cameras"]:
                cap.release()
                db.health(cam_id, "switch", "config changed")
                return True

        h0, w0 = frame.shape[:2]
        if w0 > PROC_W:
            frame = cv2.resize(frame, (PROC_W, int(h0 * PROC_W / w0)))
        h, w = frame.shape[:2]
        if poly is None:
            poly = [(p[0] * w, p[1] * h) for p in cam["poly"]]
            zone = PolygonZone(poly)
            bbox = poly_bbox(poly, w, h)
            m_full = cam.get("m_per_px_fullw", 0.075)
            m_per_px = m_full * (1920.0 / w)  # config is per full-width pixel
            speeds = SpeedBook(m_per_px)

        # one-time scene context per camera (AI, cached on disk)
        if scene is None and now - scene_try_ts > 600 and ai_analyst.enabled():
            scene_try_ts = now
            ok2, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                sc = ai_analyst.scene_context(jb.tobytes())
                if sc:
                    json.dump(sc, open(scene_path(cam_id), "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    scene = norm_scene_coords(sc)
                    with S.lock:
                        S.ticker.appendleft("AI opisał scenę перекрёстка (scene context) ✔")

        # foreground (motion) mask for this fixed camera — static objects
        # (poles, signs, traffic lights) produce ~no foreground and get gated out
        fgmask = bgsub.apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), learningRate=0.01)

        dets = det.detect(frame)
        roi_dets = detect_roi(det, frame, bbox)
        for r in roi_dets:
            if not any(_iou(r.xyxy, d.xyxy) > 0.45 and r.cls == d.cls for d in dets):
                dets.append(r)

        # drop detections that overlap a known STATIC scene object (traffic light /
        # sign / pole) — this is the scene-context "teaching the detector what to
        # ignore". Also drop absurdly small boxes.
        ignore = _scene_ignore(scene, w, h)
        kept = []
        for d in dets:
            bw = d.xyxy[2] - d.xyxy[0]; bh = d.xyxy[3] - d.xyxy[1]
            if bw * bh < 90:
                continue
            if any(_iou(d.xyxy, ig) > 0.30 for ig in ignore):
                continue
            kept.append(d)
        dets = kept

        # privacy first
        pboxes = [head_region(d.xyxy) for d in dets if d.cls == PERSON]
        pboxes += [plate_region(d.xyxy) for d in dets if d.cls == VEHICLE]
        frame = blur_regions(frame, pboxes, block=10)

        peds = [d for d in dets if d.cls == PERSON]
        vehs = [d for d in dets if d.cls == VEHICLE]
        ped_tracks = ped_tr.update(peds)
        veh_tracks = veh_tr.update(vehs)

        # which tracks show foreground motion right now
        ped_fg = {tid for tid, p in ped_tracks.items() if fg_ratio_at(fgmask, *p) >= FG_MIN}
        veh_fg = {tid for tid, p in veh_tracks.items() if fg_ratio_at(fgmask, *p) >= FG_MIN}
        # confirm real objects (moved over lifetime) — count ONLY on confirmation
        new_ped = len(ped_cb.update(ped_tracks, ped_fg))
        new_veh = len(veh_cb.update(veh_tracks, veh_fg))
        if new_ped or new_veh:
            db.bump_counts(cam_id, new_ped, new_veh, 0)
        db.bump_counts(cam_id, 0, 0, min(dt, 3.0))

        veh_kmh = speeds.update(veh_tracks, now)
        ped_speeds = SpeedBook(m_per_px) if False else None  # ped speeds via same book:
        # (pedestrian speeds tracked separately)
        if not hasattr(speeds, "_pedbook"):
            speeds._pedbook = SpeedBook(m_per_px)
        ped_kmh = speeds._pedbook.update(ped_tracks, now)
        for tid, v in veh_kmh.items():
            if v > 1.5 and int(now * 2) % 10 == 0:  # sample, don't flood
                db.add_speed(cam_id, "vehicle", v)
        for tid, v in ped_kmh.items():
            if 0.5 < v < 15 and int(now * 2) % 20 == 0:
                db.add_speed(cam_id, "pedestrian", v)

        # traffic lights
        tl_states = {}
        if scene:
            for t in scene.get("traffic_lights", [])[:4]:
                if len(t.get("bbox", [])) == 4:
                    tl_states[t.get("id", "tl")] = tl_color(frame, t["bbox"])
        tl_summary = ",".join(f"{k}:{v}" for k, v in tl_states.items()) or "unknown"

        def in_bbox(p):
            return bbox[0] <= p[0] <= bbox[2] and bbox[1] <= p[1] <= bbox[3]
        # events use ONLY confirmed (real, moved) tracks — never phantom statics
        ped_in = [p for tid, p in ped_tracks.items()
                  if zone.contains(p) and ped_cb.confirmed(tid)]
        veh_in_moving = [tid for tid, p in veh_tracks.items()
                         if in_bbox(p) and veh_cb.confirmed(tid) and speeds.is_moving(tid)]
        instant_condition = bool(ped_in) and bool(veh_in_moving)
        cond_streak = cond_streak + 1 if instant_condition else 0
        # require the conflict to persist a few frames — kills 1-frame phantoms
        condition = cond_streak >= MIN_EVENT_FRAMES

        # what to DRAW / count in-frame: only ACTIVE detections (confirmed track
        # OR foreground now) — a static pole/light is never boxed
        def det_active(d):
            ax, ay = d.anchor
            return fg_ratio_at(fgmask, ax, ay) >= FG_MIN
        active_peds = [d for d in peds if det_active(d)]
        active_vehs = [d for d in vehs if det_active(d)]

        # annotate
        ann = frame.copy()
        cv2.polylines(ann, [np.array(poly, np.int32)], True, (60, 200, 255), 2)
        for d in active_peds + active_vehs:
            x1, y1, x2, y2 = [int(v) for v in d.xyxy]
            c = (90, 230, 120) if d.cls == PERSON else (80, 150, 255)
            cv2.rectangle(ann, (x1, y1), (x2, y2), c, 2)
        ann[:44] = (ann[:44] * 0.35).astype(np.uint8)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cv2.circle(ann, (20, 22), 7, (60, 60, 235), -1)
        vmax = max(veh_kmh.values()) if veh_kmh else 0
        cv2.putText(ann, f"LIVE | piesi: {len(active_peds)}  pojazdy: {len(active_vehs)}"
                         f" | max ~{vmax:.0f} km/h | sygnalizacja: {tl_summary[:28]} | {ts}",
                    (36, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (240, 240, 240), 1, cv2.LINE_AA)
        if condition or (ep is not None):
            cv2.rectangle(ann, (0, 0), (ann.shape[1] - 1, ann.shape[0] - 1), (0, 0, 235), 5)

        # clip frame for ring buffer / episode
        small = cv2.resize(ann, (CLIP_W, int(ann.shape[0] * CLIP_W / ann.shape[1])))
        ring.append((now, small))
        while ring and now - ring[0][0] > PRE_ROLL_S:
            if ep is None:
                ring.popleft()
            else:
                break

        # ---- episode state machine ----
        if ep is None and condition and S.recording_ok:
            ep = Episode()
            ep.start = now
            ep.last_cond = now
            ep.frames = list(ring)
            with S.lock:
                S.episode_active = True
        if ep is not None:
            ep.frames.append((now, small))
            if condition:
                ep.last_cond = now
                ep.max_kmh = max(ep.max_kmh, vmax)
                ep.n_ped = max(ep.n_ped, len(ped_in))
                ep.n_veh = max(ep.n_veh, len(veh_in_moving))
                ep.tl_states.append(tl_summary)
                overlap = len(ped_in) + len(veh_in_moving)
                if overlap > ep.best_overlap:
                    ep.best_overlap = overlap
                    ep.best_snap = ann.copy()
            ended = (now - ep.last_cond > EPISODE_END_S) or (now - ep.start > EPISODE_MAX_S)
            if ended:
                if now - ep.last_cond > EPISODE_END_S:  # keep short post-roll
                    keep = [f for f in ep.frames if f[0] <= ep.last_cond + POST_ROLL_S]
                    # honest duration = span of the recorded clip (never 0)
                    dur = max(MIN_CLIP_SEC, (keep[-1][0] - keep[0][0]) if len(keep) > 1 else 0.0)
                    stamp = int(ep.start)
                    clip_name = f"ep_{cam_id}_{stamp}.mp4"
                    snap_name = f"ep_{cam_id}_{stamp}.jpg"
                    ok_clip = write_clip(os.path.join(CLIP_DIR, clip_name), keep, CLIP_FPS)
                    if ep.best_snap is not None:
                        cv2.imwrite(os.path.join(SNAP_DIR, snap_name), ep.best_snap,
                                    [cv2.IMWRITE_JPEG_QUALITY, 80])
                    tl_mode = max(set(ep.tl_states), key=ep.tl_states.count) if ep.tl_states else "unknown"
                    desc = (f"Pojazd w ruchu i pieszy jednocześnie w strefie przejścia "
                            f"(piesi: {ep.n_ped}, pojazdy w ruchu: {ep.n_veh}, "
                            f"max ~{ep.max_kmh:.0f} km/h, sygnalizacja: {tl_mode}).")
                    eid = db.add_event(cam_id, desc, snap_name,
                                       clip_name if ok_clip else None,
                                       dur, tl_mode, round(ep.max_kmh, 1),
                                       ep.n_ped, ep.n_veh)
                    with S.lock:
                        S.ticker.appendleft(f"#{eid} epizod zapisany ({dur:.0f}s) → analiza AI…")
                ep = None
                with S.lock:
                    S.episode_active = False

        with S.lock:
            S.ped_total += new_ped
            S.veh_total += new_veh
            S.in_ped, S.in_veh = len(active_peds), len(active_vehs)
            S.fps = 0.8 * S.fps + 0.2 * (1.0 / dt if dt > 0 else 0)
            S.tl = tl_states
            S.speeds_now = {
                "veh_kmh": round(max(veh_kmh.values()), 1) if veh_kmh else None,
                "ped_kmh": round(max(ped_kmh.values()), 1) if ped_kmh else None}
            ok3, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok3:
                S.jpeg = buf.tobytes()


# ---------------- HTTP ----------------
def _admin_ok(handler):
    tok = handler.headers.get("X-Admin-Token") or ""
    if not tok and "token=" in handler.path:
        tok = handler.path.split("token=")[1].split("&")[0]
    return ADMIN_TOKEN and tok == ADMIN_TOKEN


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path, ctype):
        if not os.path.isfile(path):
            return self._json(404, {"ok": False})
        with open(path, "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/healthz":
            return self._json(200, {"ok": True, "live": S.live})
        if p == "/state.json":
            with S.lock:
                el = max(1e-6, (time.time() - S.started) / 3600.0)
                d = {"live": S.live, "cam_id": S.cam_id, "source": S.cam_label,
                     "ped_total": S.ped_total, "veh_total": S.veh_total,
                     "ped_per_hour": round(S.ped_total / el, 1),
                     "veh_per_hour": round(S.veh_total / el, 1),
                     "in_frame": {"ped": S.in_ped, "veh": S.in_veh},
                     "fps": round(S.fps, 1), "tl": S.tl, "speeds": S.speeds_now,
                     "episode_active": S.episode_active,
                     "recording_ok": S.recording_ok,
                     "ticker": list(S.ticker),
                     "ai_enabled": ai_analyst.enabled(),
                     "ai_calls_today": db.ai_calls_today()}
            d["stats"] = db.stats(d["cam_id"] or None)
            d["events"] = db.list_events("all", 9, cam_id=d["cam_id"] or None)
            return self._json(200, d)
        if p == "/events.json":
            import urllib.parse
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            tab = (q.get("tab") or ["all"])[0]
            offset = int((q.get("offset") or ["0"])[0])
            return self._json(200, {"events": db.list_events(tab, 12, offset)})
        if p == "/charts.json":
            cam = S.cam_id or cams_load().get("active", "")
            return self._json(200, db.charts(cam))
        if p == "/api/stats":
            return self._json(200, db.stats())
        if p.startswith("/snap/"):
            return self._file(os.path.join(SNAP_DIR, os.path.basename(p)), "image/jpeg")
        if p.startswith("/clip/"):
            return self._file(os.path.join(CLIP_DIR, os.path.basename(p)), "video/mp4")
        if p == "/scene.json":
            cam = S.cam_id or cams_load().get("active", "")
            sc = load_scene(cam)
            return self._json(200 if sc else 404, sc or {"ok": False})
        if p == "/report.csv":
            return self._report_csv()
        if p == "/report.html":
            return self._report_html()
        if p == "/live.mjpg":
            return self._mjpeg()
        if p == "/admin/cameras":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            return self._json(200, cams_load())
        if p == "/admin/health":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            return self._json(200, db.camera_uptime_stats())
        return self._json(404, {"ok": False})

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 64 * 1024)
            data = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return self._json(400, {"ok": False})
        if p == "/api/verify":
            ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
            ok = db.vote(int(data.get("id", 0)), str(data.get("verdict", "")), ip)
            return self._json(200 if ok else 400, {"ok": ok})
        if p == "/admin/cameras":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            cfg = cams_load()
            cam = data.get("camera")
            if data.get("delete"):
                cfg["cameras"] = [c for c in cfg["cameras"] if c["id"] != data["delete"]]
            elif cam and cam.get("id") and cam.get("url"):
                cam.setdefault("poly", [[0.3, 0.6], [0.7, 0.6], [0.7, 0.85], [0.3, 0.85]])
                cam.setdefault("m_per_px_fullw", 0.075)
                others = [c for c in cfg["cameras"] if c["id"] != cam["id"]]
                cfg["cameras"] = others + [cam]
            if data.get("active"):
                cfg["active"] = data["active"]
            cams_save(cfg)
            return self._json(200, cfg)
        return self._json(404, {"ok": False})

    def _mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                with S.lock:
                    buf = S.jpeg
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                self.wfile.write(buf + b"\r\n")
                time.sleep(1.0 / max(1.0, TARGET_FPS))
        except (BrokenPipeError, ConnectionError, OSError):
            return

    def _report_csv(self):
        cam = S.cam_id or cams_load().get("active", "")
        rows = db.all_events_for_report(cam)
        lines = ["id,ts_utc,duration_s,tl_state,max_veh_kmh,status,ai_verdict,"
                 "ai_confidence,human_confirm,human_refute"]
        for r in rows:
            lines.append(",".join(str(x if x is not None else "") for x in
                                  (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8], r[9], r[10])))
        b = ("﻿" + "\n".join(lines)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename=patrol-events.csv")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _report_html(self):
        cam = S.cam_id or cams_load().get("active", "")
        st = db.stats(cam)
        ch = db.charts(cam)
        rows = db.all_events_for_report(cam, 200)
        label = S.cam_label or cam
        trs = "".join(
            f"<tr><td>#{r[0]}</td><td>{r[1]}</td><td>{r[2] or ''} s</td><td>{r[3] or ''}</td>"
            f"<td>{r[4] or ''}</td><td>{r[6] or r[5]}</td><td>{(r[7] or '')[:160]}</td>"
            f"<td>{r[9]}/{r[10]}</td></tr>" for r in rows)
        hourly = ch["hourly"][-24:]
        maxv = max([x["veh"] for x in hourly] + [1])
        bars = "".join(
            f'<div class="bar" style="height:{max(2, int(60 * x["veh"] / maxv))}px" '
            f'title="{x["h"]}: piesi {x["ped"]}, pojazdy {x["veh"]}"></div>' for x in hourly)
        html = f"""<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">
<title>Raport — Bezpieczne Przejścia — {label}</title>
<style>body{{font:14px/1.5 system-ui;margin:2rem;color:#111}}h1{{font-size:1.4rem}}
table{{border-collapse:collapse;width:100%;font-size:.8rem}}td,th{{border:1px solid #ccc;padding:.3rem .5rem;text-align:left}}
.kpi{{display:inline-block;margin:.4rem 1.2rem .4rem 0}}.kpi b{{font-size:1.5rem}}
.bars{{display:flex;align-items:flex-end;gap:2px;height:64px;margin:.5rem 0}}
.bar{{width:10px;background:#1668b0}}
.note{{background:#f6f3d6;padding:.6rem;border-radius:6px;font-size:.85rem}}</style></head><body>
<h1>🚸 Bezpieczne Przejścia — raport przejścia</h1>
<p><b>{label}</b> · wygenerowano {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ·
patrol.flyreelstudio.eu</p>
<p class="note">Demonstrator techniczny — dane orientacyjne (screening), nie dowody. Prędkości to
przybliżenie monokularne. Wersje zdarzeń zweryfikowane przez AI (Gemini) i przez ludzi. Twarze i
tablice są rozmyte u źródła.</p>
<div><span class="kpi"><b>{st['events_total']}</b><br>zdarzeń</span>
<span class="kpi"><b>{st['ai_analyzed']}</b><br>przeanalizowane przez AI</span>
<span class="kpi"><b>{st['ai_violations']}</b><br>naruszeń wg AI</span>
<span class="kpi"><b>{st['human_judged']}</b><br>ocenione przez ludzi</span>
<span class="kpi"><b>{st['ai_human_agreement_pct'] or '—'}%</b><br>zgodność AI↔ludzie</span></div>
<h2>Pojazdy na godzinę (ostatnie 24h)</h2><div class="bars">{bars}</div>
<h2>Zdarzenia (ostatnie {len(rows)})</h2>
<table><tr><th>ID</th><th>Czas (UTC)</th><th>Długość</th><th>Sygnalizacja</th>
<th>Max km/h</th><th>Werdykt AI</th><th>Wyjaśnienie AI (PL)</th><th>Głosy 👍/👎</th></tr>{trs}</table>
<p>Open source: github.com/AndriiShramko/bezpieczne-przejscia · Kontakt: Andrii Shramko,
zmei116@gmail.com, linkedin.com/in/andriishramko</p></body></html>"""
        b = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def main():
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=analyzer_loop, daemon=True).start()
    threading.Thread(target=disk_guard, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"cv-service v2 on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
