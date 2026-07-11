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
import urllib.parse

import cv2
import numpy as np

# Bound OpenCV's internal thread pool so decode/resize doesn't oversubscribe a
# small box shared with a local VLM (0 = OpenCV default = all cores).
_CV_THREADS = int(os.environ.get("CV_THREADS", "0"))
if _CV_THREADS > 0:
    cv2.setNumThreads(_CV_THREADS)

import ai_analyst
import db
from safecross.blur import blur_regions, head_region
from safecross.detect import BIKE, Detection, OnnxCocoDetector, PERSON, VEHICLE
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
BLUR = os.environ.get("BLUR", "1") not in ("0", "false", "off")
# Live MJPEG must fit a residential uplink when the node runs at home:
# 1280px @ 12 fps ≈ 16 Mbit/s per viewer and clogs the tunnel (frames then
# arrive in 2-3 s clumps). 960px @ 6 fps ≈ 3-4 Mbit/s and stays smooth.
LIVE_WIDTH = int(os.environ.get("LIVE_WIDTH", "960"))
LIVE_FPS = float(os.environ.get("LIVE_FPS", "6"))
# constant playout latency: must cover one HLS segment + processing burst
LIVE_DELAY_S = float(os.environ.get("LIVE_DELAY_S", "4.5"))
EPISODE_COOLDOWN_S = float(os.environ.get("EPISODE_COOLDOWN_S", "20"))
SPEED_LIMIT_KMH = float(os.environ.get("SPEED_LIMIT_KMH", "50"))
# monocular speed is ±30%: flag only well above the limit to avoid slander
SPEED_FLAG_FACTOR = float(os.environ.get("SPEED_FLAG_FACTOR", "1.35"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://patrol.flyreelstudio.eu").rstrip("/")
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

# ---------------- global (universal) event rules ----------------
# One shared ruleset the AI reads for EVERY event on EVERY camera, on top of any
# per-crossing rules. This is where the lessons from confirmed false positives
# live, so a NEW camera starts with the full body of "do not false-trigger"
# knowledge without any per-camera setup. Editable in /admin.html.
GLOBAL_RULES_FILE = os.path.join(DATA, "global_rules.txt")
DEFAULT_GLOBAL_RULES = """UNIVERSAL RULES — apply to every pedestrian crossing, on every camera.
1. A violation REQUIRES a real, clearly MOVING MOTOR VEHICLE (car, bus, truck, motorcycle)
   driving across the crossing. If you cannot actually see such a vehicle in the frames, the
   verdict is no_violation. Never assume, infer or hallucinate a vehicle that is not visible.
2. A lone pedestrian, cyclist or motorcyclist — with NO conflicting motor vehicle — is NEVER a
   violation. People and cyclists are allowed to be on the crossing.
3. A person RIDING a bicycle or motorcycle is a rider, NOT a pedestrian. A rider does not create
   a pedestrian-yield event "on themselves". A motorcycle/scooter is a motor vehicle that must
   yield to others, but its rider is never the endangered pedestrian.
4. Cyclists using their OWN marked bike crossing (przejazd dla rowerzystów), parallel to the
   zebra, are crossing lawfully — they are not pedestrians on the zebra and do not conflict with
   vehicles on unrelated lanes.
5. The driver must yield ONLY while the pedestrian is on the ROADWAY (jezdnia). Once the
   pedestrian has reached the far sidewalk/kerb or a refuge island — even if still near the
   painted stripes — a vehicle proceeding is NOT a violation.
6. A vehicle stopped/creeping while WAITING (red light, or letting a pedestrian pass) is not a
   violation. Only a vehicle actually driving through the pedestrian's path violates.
7. A pedestrian standing on a refuge island has left the crossing half behind them; a vehicle on
   that already-cleared half does not conflict with them.
8. Judge by the REAL road surface and real positions, not by an over-stretched or miscalibrated
   zone polygon. A person on the sidewalk who merely falls inside the drawn zone is not a
   crossing user.
9. Prams/strollers, wheelchairs and shopping carts are NOT motor vehicles.
"""


def load_global_rules():
    try:
        with open(GLOBAL_RULES_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    except OSError:
        pass
    try:   # seed on first run so the admin can see & refine the baseline
        with open(GLOBAL_RULES_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_GLOBAL_RULES)
    except OSError:
        pass
    return DEFAULT_GLOBAL_RULES


def save_global_rules(text):
    with open(GLOBAL_RULES_FILE, "w", encoding="utf-8") as f:
        f.write(str(text))


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
        try:
            with open(CAMS_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict) and cfg.get("cameras"):
                return cfg
        except (OSError, ValueError):
            pass
        # missing / empty / corrupt file -> self-heal with defaults (a config
        # sync or admin edit will overwrite them shortly)
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
        self.bike_total = 0
        self.in_ped = 0
        self.in_veh = 0
        self.in_bike = 0
        self.fps = 0.0
        self.tl = {}
        self.speeds_now = {"veh_kmh": None, "ped_kmh": None}
        self.ticker = deque(maxlen=8)
        self.recording_ok = True
        self.episode_active = False
        self.last_frame_ts = 0.0
        self.frame_raw = None     # clean frame (no overlay) for the zone editor


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
    JUMP_PX = 150       # one-step displacement above this = tracker jumped to
                        # ANOTHER object -> reset history (kills fake 100 km/h)
    MAX_KMH = 140       # urban camera: anything above is a measurement artifact
    PED_HEIGHT_M = 1.70  # assumed pedestrian height — used as a per-frame ruler

    def __init__(self, m_per_px, scale_fn=None, walker=False):
        self.m_per_px = m_per_px
        self.scale_fn = scale_fn        # optional m_per_px(y) — perspective
        self.walker = walker            # pedestrians: path-length (radial-aware)
        self.hist = {}  # tid -> deque[(t, x, y, box_h)]

    def _mpp(self, y):
        return self.scale_fn(y) if self.scale_fn else self.m_per_px

    def update(self, tracks, now, heights=None):
        out = {}
        self.stationary = set()
        stat_px = max(6.0, (MOVE_KMH_MIN / 3.6) / max(1e-6, self.m_per_px) * self.STAT_SEC)
        for tid, (x, y) in tracks.items():
            q = self.hist.setdefault(tid, deque(maxlen=16))
            if q:
                step = ((x - q[-1][1]) ** 2 + (y - q[-1][2]) ** 2) ** 0.5
                if step > self.JUMP_PX:
                    q.clear()           # identity switch — start fresh
            q.append((now, x, y, float((heights or {}).get(tid, 0.0) or 0.0)))
            if len(q) >= 3 and q[-1][0] - q[0][0] >= 0.8:
                t0, x0, y0 = q[0][0], q[0][1], q[0][2]
                dt = now - t0
                if dt > 0.2:  # never divide by a degenerate window
                    d_px = float(((x - x0) ** 2 + (y - y0) ** 2) ** 0.5)
                    if self.walker:
                        # a pedestrian walking toward/away from the camera barely
                        # shifts its centroid — straight displacement then reads
                        # a silly ~2 km/h. Use accumulated foot-path instead
                        # (captures step-to-step motion), floored at displacement.
                        path = 0.0
                        for i in range(1, len(q)):
                            path += ((q[i][1] - q[i-1][1]) ** 2
                                     + (q[i][2] - q[i-1][2]) ** 2) ** 0.5
                        d_px = max(d_px, 0.7 * path)
                    # SCALE: for a pedestrian, the most reliable, calibration-free
                    # ruler is the person's OWN bounding-box height — ~1.70 m tall,
                    # so metres-per-pixel = 1.70 / box_height at THAT distance. This
                    # is perspective-correct from the very first frame (near = tall
                    # box = small m/px, far = short box = large m/px), which fixes
                    # the "same walker reads 2 km/h far and 6 km/h near" bug on
                    # cameras with no horizon calibration and sparse autoscale data.
                    # Vehicles keep the row-scale (their height is unreliable).
                    hs = [p[3] for p in q if p[3] > 8.0]
                    if self.walker and hs:
                        mpp = self.PED_HEIGHT_M / (sorted(hs)[len(hs) // 2])
                    else:
                        mpp = self._mpp((y0 + y) / 2.0)
                    kmh = float((d_px / dt) * mpp * 3.6)
                    if kmh <= self.MAX_KMH:
                        out[tid] = kmh
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

    def heading_ok(self, tid, cx, cy, recede_px=12.0):
        """True unless the track is clearly a TURN-AWAY — a car that turns onto
        another road or reverses out, rather than driving across the crossing.
        The direction is judged from the track's ENTRY point toward the zone
        centre, NOT its current position: a car driving straight through keeps
        heading toward where it entered aiming, so it stays valid for the whole
        transit (only a genuine turn/U-turn flips the sign). Zone membership
        (contains) already handles 'already past the zebra'. Unknown/short
        tracks return True (never hide a real crosser)."""
        q = self.hist.get(tid)
        if not q or len(q) < 3:
            return True
        x0, y0 = q[0][1], q[0][2]
        x1, y1 = q[-1][1], q[-1][2]
        mx, my = x1 - x0, y1 - y0
        mlen = (mx * mx + my * my) ** 0.5
        if mlen < 4.0:
            return True                       # essentially not translating
        tx, ty = cx - x0, cy - y0             # ENTRY point -> zone centre
        tlen = (tx * tx + ty * ty) ** 0.5
        if tlen < 4.0:
            return True                       # entered right at the centre
        # cosine between heading and entry->centre direction: a straight
        # crosser is ~+1 the whole way; a turn-away goes negative
        cos = (mx * tx + my * ty) / (mlen * tlen)
        return cos > -0.35


class ConfirmBook:
    """Confirms a track is a REAL object only after it has been tracked for
    >=min_frames AND has actually MOVED >=min_move_px over its lifetime. This
    is what kills static false positives (a pole/sign/traffic-light misread as
    person/car never moves, so it is never confirmed and never counted) and
    single-frame phantom flickers.

    Resurrection: when a confirmed track dies (detector flicker) and a NEW
    track appears near the same spot within a few seconds, the new track
    INHERITS the confirmed flag without being counted again — so a flickering
    box on one car adds 1 to the counter, not 3."""

    GRAVE_SEC = 5.0
    GRAVE_PX = 70.0

    def __init__(self, min_frames, min_move_px):
        self.min_frames = min_frames
        self.min_move = min_move_px
        self.info = {}
        self.newly = set()
        self.grave = deque(maxlen=64)  # (t_death, x, y) of confirmed tracks

    def update(self, tracks, fg_hits, now=None):
        now = now if now is not None else time.time()
        self.newly = set()
        for tid, (x, y) in tracks.items():
            e = self.info.get(tid)
            if e is None:
                e = {"n": 0, "x0": x, "y0": y, "maxd": 0.0, "ok": False, "fg": 0}
                # a confirmed object died here moments ago AND this new track
                # shows motion right now? -> same object after a detector
                # flicker: inherit confirmation, do NOT count again. The grave
                # entry is CONSUMED (one grave = one inheritance) so a stream
                # of distinct objects through one spot is still counted.
                if tid in fg_hits:
                    for i, (td, gx, gy) in enumerate(self.grave):
                        if now - td <= self.GRAVE_SEC and \
                           ((x - gx) ** 2 + (y - gy) ** 2) ** 0.5 <= self.GRAVE_PX:
                            e["ok"] = True
                            del self.grave[i]
                            break
                self.info[tid] = e
            e["n"] += 1
            d = ((x - e["x0"]) ** 2 + (y - e["y0"]) ** 2) ** 0.5
            if d > e["maxd"]:
                e["maxd"] = d
            if tid in fg_hits:
                e["fg"] += 1
            e["last"] = (x, y)
            if not e["ok"] and e["n"] >= self.min_frames and e["maxd"] >= self.min_move:
                e["ok"] = True
                self.newly.add(tid)
        for tid in list(self.info):
            if tid not in tracks:
                e = self.info.pop(tid)
                # only tracks that EARNED confirmation by moving in this life
                # re-arm the grave — an inherited-but-static track cannot chain
                if e.get("ok") and e["maxd"] >= self.min_move and "last" in e:
                    self.grave.append((now, e["last"][0], e["last"][1]))
        return self.newly

    def confirmed(self, tid):
        e = self.info.get(tid)
        return bool(e and e["ok"])

    def active(self, tid):
        """Confirmed (has moved) OR currently showing foreground — used to
        decide what to DRAW, so we never box a static pole."""
        e = self.info.get(tid)
        return bool(e and (e["ok"] or e["fg"] > 0))


class AutoScale:
    """Self-calibrating meters-per-pixel, per image row — no buttons, no AI
    calls, works on ANY camera. Every confirmed moving object is a ruler:
    a pedestrian is ~1.7 m tall, a car's shorter silhouette side is ~1.75 m.
    Medians per horizontal band converge within minutes of normal traffic and
    persist to disk. Fallback order: measured bands -> AI-horizon model ->
    constant configured scale."""

    BANDS = 8
    PED_M = 1.70
    VEH_M = 1.75
    MIN_SAMPLES = 25

    def __init__(self, cam_id, h, fallback):
        self.cam_id = cam_id
        self.h = h
        self.fallback = fallback            # scale_fn(y) or None
        self.samples = [deque(maxlen=300) for _ in range(self.BANDS)]
        self.path = os.path.join(SCENE_DIR, f"autoscale_{cam_id}.json")
        self._scale_cache = {}
        self._last_save = 0.0
        try:
            data = json.load(open(self.path, encoding="utf-8"))
            for i, vals in enumerate(data.get("samples", [])[:self.BANDS]):
                self.samples[i].extend(float(v) for v in vals[-300:])
        except (OSError, ValueError):
            pass

    def _band(self, y):
        return max(0, min(self.BANDS - 1, int(y / self.h * self.BANDS)))

    def feed(self, dets_peds, dets_vehs):
        for d in dets_peds:
            x1, y1, x2, y2 = d.xyxy
            hh = y2 - y1
            if hh > 14:
                self.samples[self._band((y1 + y2) / 2)].append(self.PED_M / hh)
        for d in dets_vehs:
            x1, y1, x2, y2 = d.xyxy
            side = min(x2 - x1, y2 - y1)
            if side > 14:
                self.samples[self._band((y1 + y2) / 2)].append(self.VEH_M / side)
        self._scale_cache = {}
        if time.time() - self._last_save > 300:
            self._last_save = time.time()
            try:
                json.dump({"samples": [list(s) for s in self.samples]},
                          open(self.path, "w", encoding="utf-8"))
            except OSError:
                pass

    def _band_scale(self, i):
        if i in self._scale_cache:
            return self._scale_cache[i]
        s = self.samples[i]
        out = None
        if len(s) >= self.MIN_SAMPLES:
            out = sorted(s)[len(s) // 2]
        self._scale_cache[i] = out
        return out

    def scale(self, y):
        i = self._band(y)
        v = self._band_scale(i)
        if v is None:
            # nearest calibrated band, corrected by the perspective prior
            for off in range(1, self.BANDS):
                for j in (i - off, i + off):
                    if 0 <= j < self.BANDS:
                        vj = self._band_scale(j)
                        if vj is not None:
                            if self.fallback:
                                yc_i = (i + 0.5) * self.h / self.BANDS
                                yc_j = (j + 0.5) * self.h / self.BANDS
                                fj = self.fallback(yc_j)
                                return vj * (self.fallback(yc_i) / fj) if fj else vj
                            return vj
            return self.fallback(y) if self.fallback else None
        return v


def make_scale(zones, scene, h, m_per_px):
    """Perspective model anchored at the main crossing: meters-per-pixel grows
    toward the horizon and shrinks toward the camera. horizon_y comes from the
    AI scene calibration; without it the scale stays constant (old behavior).
    This is what stopped roundabout traffic near the camera from reading
    ~100 km/h."""
    if not zones:
        return None
    y_ref = sum(p[1] for p in zones[0]["poly"]) / len(zones[0]["poly"])
    hy = None
    if isinstance(scene, dict):
        try:
            hv = float(scene.get("horizon_y"))
            if 0.0 < hv < 0.9:
                hy = hv * h
        except (TypeError, ValueError):
            pass
    if hy is None or hy >= y_ref - 30:
        return None
    def fn(y):
        f = (y_ref - hy) / max(25.0, y - hy)
        return m_per_px * min(4.0, max(0.25, f))
    return fn


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
        self.paths = {}           # "p<tid>"/"v<tid>"/"b<tid>" -> [(t,x,y), ...]

    def paths_json(self, max_chars=2400):
        """Compact normalized trajectories for the AI verdict — the motion
        story the frames alone can't tell at 2-3 fps."""
        items = sorted(self.paths.items(), key=lambda kv: -len(kv[1]))
        out = {}
        for k, v in items:
            if len(v) < 3:
                continue
            out[k] = v[::max(1, len(v) // 12)][:12]   # ≤12 evenly picked points
            if len(json.dumps(out)) > max_chars:
                out.pop(k)
                break
        return json.dumps(out, separators=(",", ":"))


def _transcode_h264(path):
    """cv2.VideoWriter writes MPEG-4 Part 2 ('mp4v') which BROWSERS CANNOT
    PLAY. Re-encode to H.264 (avc1) + faststart via the static ffmpeg binary
    shipped with imageio-ffmpeg. Runs in a background thread per clip."""
    try:
        import subprocess
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        tmp = path + ".h264.mp4"
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
            check=True, timeout=180)
        os.replace(tmp, path)
    except Exception as e:
        print("transcode:", e, flush=True)


def write_clip(path, frames, fps=None):
    """Frames carry content timestamps but are NOT uniformly spaced (pre-roll
    was sampled at a different stride than the episode). Writing them 1:1 at a
    constant fps makes parts play too fast/slow. Fix: RESAMPLE onto a uniform
    time grid (nearest-previous frame per tick) — the clip then plays back in
    true real time throughout."""
    if not frames:
        return False
    if fps is None:
        fps = max(2.0, min(8.0, CLIP_FPS))
    span = frames[-1][0] - frames[0][0]
    if span > 0.4 and len(frames) > 2:
        grid = []
        t = frames[0][0]
        j = 0
        while t <= frames[-1][0] + 1e-6:
            while j + 1 < len(frames) and frames[j + 1][0] <= t:
                j += 1
            grid.append(frames[j][1])
            t += 1.0 / fps
        out_frames = grid
    else:
        out_frames = [f for _, f in frames]
    h, w = out_frames[0].shape[:2]
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in out_frames:
        vw.write(f)
    vw.release()
    ok = os.path.getsize(path) > 0
    if ok:  # make it browser-playable off the frame loop
        threading.Thread(target=_transcode_h264, args=(path,), daemon=True).start()
    return ok


class Grabber:
    """Dedicated stream-reader thread with a small thinned queue.

    Live HLS delivers frames in SEGMENT BURSTS (a 2-3 s segment downloads and
    decodes in a fraction of a second, then nothing until the next segment).
    A keep-only-latest slot therefore yields ~1 usable frame per segment
    (~0.3-0.8 fps). Instead we keep every Nth frame in a bounded deque: the
    processing loop consumes them evenly at TARGET_FPS with a constant ~one
    segment of latency — smooth video, no freezes, no multi-minute jumps."""

    def __init__(self, cap, target_fps):
        self.cap = cap
        self.lock = threading.Lock()
        self.q = deque(maxlen=40)          # ~3-4 s of thinned frames
        self.t = 0.0                       # last successful read (stall detect)
        self.reads = 0
        self.fails = 0
        self.stop = False
        src = cap.get(cv2.CAP_PROP_FPS) or 0
        self.src_fps = src if 1 <= src <= 120 else 25.0
        want = min(12.0, max(2.0, 2.0 * target_fps))   # buffer a bit above need
        self.keep_every = max(1, int(round(self.src_fps / want)))
        self.anchor = None                 # wall time of frame #0 (content clock)
        self.seq = 0
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        last_cts = 0.0
        fps_win = deque(maxlen=600)   # (wall, seq) — empirical source-fps probe
        try:
            while not self.stop:
                ok, f = self.cap.read()
                if ok:
                    now = time.time()
                    self.reads += 1
                    self.t = now
                    if self.anchor is None:
                        self.anchor = now
                    # empirical source fps: CAP_PROP_FPS lies on some HLS cams
                    # (0 or wrong), which would scale every speed estimate.
                    # Measure delivered frames over a >=12 s window instead —
                    # segment bursts average out across several segments.
                    fps_win.append((now, self.seq))
                    if len(fps_win) > 60 and now - fps_win[0][0] >= 25.0 \
                            and now - getattr(self, "_fps_sw", 0) > 60.0:
                        est = (self.seq - fps_win[0][1]) / (now - fps_win[0][0])
                        if 1.0 <= est <= 120.0 and abs(est - self.src_fps) / self.src_fps > 0.12:
                            self._fps_sw = now
                            print(f"grabber: src_fps {self.src_fps:.1f} -> "
                                  f"{est:.1f} (measured)", flush=True)
                            # keep the content clock continuous at switch time
                            self.anchor = (self.anchor + self.seq / self.src_fps
                                           - self.seq / est)
                            self.src_fps = est
                    # CONTENT timestamp: frames arrive in segment bursts, but
                    # in the video they are spaced 1/src_fps apart. All
                    # kinematics (speeds, episode timing, clip fps) use this
                    # clock, so bursty ARRIVAL never distorts them.
                    cts = self.anchor + self.seq / self.src_fps
                    # ONE-SIDED re-anchor: only when the reader LAGS wall time
                    # (stall/reconnect). Running AHEAD during a backlog burst
                    # is just constant latency — snapping the clock backwards
                    # would corrupt speeds and drop frames.
                    if now - cts > 5.0:
                        self.anchor = now - self.seq / self.src_fps
                        cts = now
                    cts = max(cts, last_cts + 1.0 / self.src_fps)  # monotonic
                    last_cts = cts
                    self.seq += 1
                    if self.seq % self.keep_every == 0:
                        with self.lock:
                            self.q.append((cts, f))
                else:
                    self.fails += 1
                    time.sleep(0.08)
        finally:
            # the READER owns the capture: releasing here (and only here)
            # can never race an in-flight cap.read() — cv2.VideoCapture is
            # not thread-safe and release-during-read can segfault natively.
            try:
                self.cap.release()
            except Exception:
                pass

    def next_due(self, due_cts):
        """Oldest frame whose content time reached `due_cts`; older ones are
        dropped. This yields EVENLY spaced frames in content time — steady
        fps, honest speeds, smooth clips."""
        with self.lock:
            while self.q:
                cts, f = self.q.popleft()
                if cts + 1e-4 >= due_cts:
                    return cts, f
            return None

    def qlen(self):
        with self.lock:
            return len(self.q)

    def close(self):
        self.stop = True
        self.th.join(timeout=3)
        # if the thread is still blocked inside a network read, it will
        # release the capture itself when the read returns (rw_timeout
        # bounds that); never call cap.release() from this thread.


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
    if not isinstance(scene, dict):
        return []
    out = []
    for t in (scene.get("traffic_lights") or []):
        b = t.get("bbox") if isinstance(t, dict) else None
        if isinstance(b, (list, tuple)) and len(b) == 4:
            out.append((b[0] * w, b[1] * h, b[2] * w, b[3] * h))
    for r in (scene.get("ignore_regions") or []):
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


def _known_cam(cid):
    """Return cid only if it is a REGISTERED camera id — used to scope the zone
    editor to a specific camera. Guards against typos AND path traversal in the
    scene/frame filenames (cid goes into scene_<cid>.json / frame_<cid>.jpg)."""
    cid = str(cid or "").strip()
    if not cid:
        return ""
    try:
        ids = {c.get("id") for c in cams_load().get("cameras", [])}
    except Exception:
        ids = set()
    return cid if cid in ids else ""


_scene_lock = threading.Lock()
_scene_busy = set()


def _draw_grid(img):
    """0.1-step labeled coordinate grid — dramatically improves the precision
    of polygon coordinates a VLM returns."""
    g = img.copy()
    h, w = g.shape[:2]
    for i in range(1, 10):
        x = int(w * i / 10); y = int(h * i / 10)
        cv2.line(g, (x, 0), (x, h), (255, 255, 255), 1)
        cv2.line(g, (0, y), (w, y), (255, 255, 255), 1)
        cv2.putText(g, f"{i/10:.1f}", (x + 3, 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(g, f"{i/10:.1f}", (3, y - 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return g


def _draw_scene_polys(img, scene):
    """Render the model's polygons for the self-check round: crossings yellow,
    bike crossings cyan, islands magenta."""
    g = img.copy()
    h, w = g.shape[:2]
    def draw(items, color):
        for c in items:
            pts = [(int(p[0] * w), int(p[1] * h)) for p in (c.get("polygon") or [])]
            if len(pts) >= 3:
                cv2.polylines(g, [np.array(pts, np.int32)], True, color, 2)
                cv2.putText(g, str(c.get("id", "?")), pts[0],
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    draw(scene.get("crossings") or [], (0, 230, 255))
    draw(scene.get("bike_crossings") or [], (255, 255, 0))
    draw(scene.get("islands") or [], (255, 0, 255))
    return g


def _rules_worker(cam_id, jpeg_bytes, scene_copy, force=False):
    """Background: generate event_rules for an existing zone map (hand-drawn or
    legacy) and merge them into the scene file — unless the admin edited the
    file meanwhile. With force=True (the '📜 fill rules' button) it OVERWRITES
    existing rules; otherwise it only fills when rules are absent."""
    try:
        try:
            mtime0 = os.path.getmtime(scene_path(cam_id))
        except OSError:
            return
        rules = ai_analyst.scene_rules(jpeg_bytes, scene_copy)
        if not rules or not isinstance(rules, str):
            return
        try:
            if os.path.getmtime(scene_path(cam_id)) != mtime0:
                return              # admin saved meanwhile — their file wins
            sc = json.load(open(scene_path(cam_id), encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(sc, dict) or (sc.get("event_rules") and not force):
            return
        sc["event_rules"] = rules
        sc["_rules_tried"] = time.time()
        json.dump(sc, open(scene_path(cam_id), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        with S.lock:
            S.ticker.appendleft("AI uzupełnił reguły zdarzeń dla stref ✔")
        print(f"rules_worker: event_rules filled for {cam_id}", flush=True)
    except Exception as e:
        print("rules_worker:", e, flush=True)


def _valid_scene(sc):
    if not (isinstance(sc, dict) and sc):
        return None
    try:
        return norm_scene_coords(json.loads(json.dumps(sc)))
    except Exception as e:
        print("scene_worker: off-schema scene rejected:", e, flush=True)
        return None


def _scene_worker(cam_id, jpeg_bytes):
    """Background scene calibration: grid-annotated frame -> scene JSON ->
    draw the polygons back -> one AI self-check round -> persist."""
    with _scene_lock:
        if cam_id in _scene_busy:
            return
        _scene_busy.add(cam_id)
    try:
        # remember what was on disk when we started: if the ADMIN saves a
        # hand-edited map while the AI is still thinking, the AI result must
        # be DISCARDED — never overwrite human corrections
        try:
            mtime0 = os.path.getmtime(scene_path(cam_id))
        except OSError:
            mtime0 = None
        arr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return
        ok, gj = cv2.imencode(".jpg", _draw_grid(img), [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return
        sc = _valid_scene(ai_analyst.scene_context(gj.tobytes()))
        if not sc:
            return
        # self-check: show the model its own polygons and let it correct them
        try:
            ok2, aj = cv2.imencode(".jpg", _draw_grid(_draw_scene_polys(img, sc)),
                                   [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok2:
                refined = _valid_scene(ai_analyst.scene_refine(aj.tobytes(), sc))
                if refined and refined.get("crossings"):
                    sc = refined
        except Exception as e:
            print("scene refine skipped:", e, flush=True)
        try:
            mtime1 = os.path.getmtime(scene_path(cam_id))
        except OSError:
            mtime1 = None
        if mtime1 is not None and mtime1 != mtime0:
            print("scene_worker: admin edited the map meanwhile — AI result "
                  "discarded", flush=True)
            return
        json.dump(sc, open(scene_path(cam_id), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        with S.lock:
            S.ticker.appendleft("AI skalibrował strefy przejść (scene v2) ✔")
    except Exception as e:
        print("scene_worker:", e, flush=True)
    finally:
        with _scene_lock:
            _scene_busy.discard(cam_id)


def load_scene(cam_id):
    p = scene_path(cam_id)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def load_scene_safe(cam_id):
    """load + normalize; a file that fails to normalize is deleted so it can
    never crash-loop the worker across restarts."""
    sc = load_scene(cam_id)
    if not sc:
        return None
    try:
        out = norm_scene_coords(sc)
        return out or None
    except Exception as e:
        print("scene file rejected (self-heal, deleting):", e, flush=True)
        try:
            os.remove(scene_path(cam_id))
        except OSError:
            pass
        return None


def norm_scene_coords(scene):
    """Defensive: the model sometimes mixes pixels and fractions, returns
    malformed points, null arrays, non-dict items or NaN. Normalize what is
    clean, drop everything else — this must NEVER raise (a persisted
    off-schema scene file would crash-loop the worker otherwise)."""
    import math

    def nrm(v, size=1920):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return v / size if v > 1.5 else v

    def lst(key):
        v = scene.get(key)
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    if not isinstance(scene, dict):
        return {}
    scene["crossings"] = lst("crossings")
    scene["traffic_lights"] = lst("traffic_lights")
    scene["bike_crossings"] = lst("bike_crossings")
    scene["islands"] = lst("islands")
    for c in scene["crossings"] + scene["bike_crossings"] + scene["islands"]:
        pts = []
        poly = c.get("polygon")
        for p in (poly if isinstance(poly, list) else []):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append([nrm(p[0]), nrm(p[1], 1080)])
        c["polygon"] = pts
    for t in scene["traffic_lights"]:
        b = t.get("bbox")
        if isinstance(b, (list, tuple)) and len(b) == 4:
            t["bbox"] = [nrm(b[0]), nrm(b[1], 1080), nrm(b[2]), nrm(b[3], 1080)]
        else:
            t["bbox"] = []
    regs = scene.get("ignore_regions")
    out_regs = []
    for r in (regs if isinstance(regs, list) else []):
        b = r.get("bbox") if isinstance(r, dict) else r
        if isinstance(b, (list, tuple)) and len(b) == 4:
            out_regs.append({"label": (r.get("label", "") if isinstance(r, dict) else ""),
                             "bbox": [nrm(b[0]), nrm(b[1], 1080),
                                      nrm(b[2]), nrm(b[3], 1080)]})
    scene["ignore_regions"] = out_regs
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


# ---------------- config sync (multi-node) ----------------
# The cloud node is the single CONFIG AUTHORITY (cameras + zone maps). A GPU
# node behind the failover proxy syncs from it every ~15 s, so admin edits
# apply to BOTH nodes no matter which one happens to serve the site.
CONFIG_SYNC_URL = os.environ.get("CONFIG_SYNC_URL", "").rstrip("/")


def config_sync_loop():
    import urllib.request
    while True:
        time.sleep(15)
        try:
            req = urllib.request.Request(
                CONFIG_SYNC_URL + "/admin/export",
                headers={"X-Admin-Token": ADMIN_TOKEN})
            data = json.load(urllib.request.urlopen(req, timeout=10))
            cams = data.get("cameras")
            if isinstance(cams, dict) and cams.get("cameras"):
                try:
                    cur = cams_load()
                except Exception:
                    cur = None   # broken local file must never block the sync
                if cams != cur:
                    cams_save(cams)
                    print("config_sync: cameras updated from authority", flush=True)
            for cid, sc in (data.get("scenes") or {}).items():
                if not (isinstance(sc, dict) and sc):
                    continue
                p = scene_path(os.path.basename(cid))
                cur = None
                try:
                    cur = json.load(open(p, encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                if sc != cur:
                    json.dump(sc, open(p, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    print(f"config_sync: scene {cid} updated from authority",
                          flush=True)
            gr = data.get("global_rules")
            if isinstance(gr, str) and gr.strip() and gr.strip() != load_global_rules().strip():
                save_global_rules(gr.strip())
                print("config_sync: global rules updated from authority", flush=True)
        except Exception as e:
            print("config_sync:", e, flush=True)


# ---------------- camera playlist ----------------
def playlist_loop():
    """Rotates the ACTIVE camera through a configured list every N minutes.
    Runs ONLY on the config-authority node — secondary nodes follow via
    config_sync, so both nodes always show the same camera."""
    while True:
        time.sleep(20)
        try:
            cfg = cams_load()
            pl = cfg.get("playlist") or {}
            if not pl.get("enabled"):
                continue
            ids = {c["id"] for c in cfg["cameras"]}
            rota = [c for c in (pl.get("cameras") or []) if c in ids]
            if len(rota) < 2:
                continue
            iv = max(1.0, float(pl.get("interval_min", 10))) * 60.0
            if time.time() - float(pl.get("last_switch", 0)) < iv:
                continue
            cur = cfg.get("active")
            nxt = rota[(rota.index(cur) + 1) % len(rota)] if cur in rota else rota[0]
            cfg["active"] = nxt
            pl["last_switch"] = time.time()
            cfg["playlist"] = pl
            cams_save(cfg)
            print(f"playlist: -> {nxt}", flush=True)
        except Exception as e:
            print("playlist:", e, flush=True)


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
    attempts = {}  # eid -> failed tries while AI was up (bad clip etc.)
    while True:
        try:
            row = db.next_pending_ai()
        except Exception as e:
            print("analyzer db error:", e, flush=True)
            time.sleep(10)
            continue
        if not row:
            time.sleep(5)
            continue
        eid, cam_id, clip, tl_state, kmh, n_ped, n_veh, ev_kind, traj = row
        try:
            path = os.path.join(CLIP_DIR, clip or "")
            if not clip or not os.path.exists(path):
                db.set_ai_skipped(eid)
                continue
            if not ai_analyst.enabled():
                time.sleep(30)
                continue
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
                                           fps=CLIP_FPS, kind=ev_kind or "potential_conflict",
                                           trajectories=traj, global_rules=load_global_rules())
            if res:
                db.set_ai_result(eid, res["verdict"], res.get("violator", "none"),
                                 res.get("explanation_pl", ""), res.get("explanation_en", ""),
                                 float(res.get("confidence", 0)))
                vul = res.get("vulnerable")
                if isinstance(vul, dict):
                    db.merge_flags(eid, vul)   # children / strollers / wheelchairs
                if res.get("phone_suspect") is True:
                    db.merge_flags(eid, {"phone": True})
                with S.lock:
                    S.ticker.appendleft(
                        f"AI #{eid}: {'NARUSZENIE' if res['verdict']=='violation' else ('OK/fałszywy alarm' if res['verdict']=='no_violation' else 'niepewne')}")
                print(f"AI verdict #{eid}: {res['verdict']}", flush=True)
                attempts.pop(eid, None)
            else:
                # None = no verdict. If AI became unavailable DURING the call
                # (429 tripping the quota pause, breaker) the event is fine —
                # leave it pending, it will be analyzed after the reset.
                if not ai_analyst.enabled():
                    time.sleep(30)
                    continue
                attempts[eid] = attempts.get(eid, 0) + 1
                if attempts.get(eid, 0) >= 3:   # genuinely unanalyzable clip
                    db.set_ai_skipped(eid)
                    attempts.pop(eid, None)
                else:
                    time.sleep(10)
        except Exception as e:
            print("analyzer:", e, flush=True)
            attempts[eid] = attempts.get(eid, 0) + 1
            if attempts.get(eid, 0) >= 3:
                db.set_ai_skipped(eid)
                attempts.pop(eid, None)
            else:
                time.sleep(10)


# ---------------- main processing loop ----------------
def worker():
    det = OnnxCocoDetector(MODEL, conf_thres=CONF)
    frame_interval = 1.0 / TARGET_FPS
    fail_start = None

    while True:
        try:
            cfg = cams_load()
            by_id = {c["id"]: c for c in cfg["cameras"]}
            order = [cfg.get("active")] + [c["id"] for c in cfg["cameras"]
                                           if c["id"] != cfg.get("active")]
        except Exception as e:
            print("worker: cams config error:", e, flush=True)
            time.sleep(10)
            continue
        cam = None
        for cid in order:
            if cid in by_id:
                cam = by_id[cid]
                try:
                    if _open_and_run(det, cam, frame_interval, by_id, cfg):
                        break  # config changed / stream ended cleanly -> reload config
                except Exception as e:
                    # the frame loop must NEVER die silently: log, mark camera
                    # down and fail over / retry — the thread itself survives.
                    import traceback
                    traceback.print_exc()
                try:
                    db.health(cid, "down", "stream failed")
                except Exception:
                    pass
        if cam is None:
            time.sleep(5)
        S.live = False
        time.sleep(3)


def _open_and_run(det, cam, frame_interval, by_id, cfg):
    """Run one camera until failure (False) or admin switched active (True)."""
    cam_id = cam["id"]
    # rw_timeout bounds a single blocked FFmpeg network read (15 s) so the
    # Grabber thread can always observe stop and release the capture itself.
    opts = "rw_timeout;15000000"
    if cam.get("referer"):
        opts += "|referer;" + cam["referer"]
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
    cap = cv2.VideoCapture(cam["url"], cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False
    grab = Grabber(cap, TARGET_FPS)
    pub = Publisher()
    try:
        return _run_camera(det, cam, frame_interval, cfg, grab, pub)
    finally:
        pub.stop = True
        grab.close()          # idempotent; guarantees no Grabber leak on ANY exit
        with S.lock:
            S.live = False


class Publisher:
    """Constant-latency playout for the live view. Processing takes a variable
    0.1-0.5 s per frame, so pushing frames to viewers as they finish looks
    jerky (speed-up / slow-down). Instead each annotated frame is released on
    a schedule derived from its CONTENT timestamp + a fixed delay — the public
    stream advances perfectly evenly."""

    def __init__(self):
        self.q = deque(maxlen=120)
        self.lock = threading.Lock()
        self.stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, cts, jpg):
        with self.lock:
            self.q.append((cts, jpg))

    def _run(self):
        # deterministic playout: a frame is shown exactly LIVE_DELAY_S after
        # its content time. No adaptive state = nothing to oscillate: earlier
        # adaptive designs alternated long-wait -> queue-overflow -> frame
        # dumps and the stream updated once per 3-10 s.
        while not self.stop:
            time.sleep(0.08)
            tgt = time.time() - LIVE_DELAY_S
            newest = None
            with self.lock:
                while self.q and self.q[0][0] <= tgt:
                    newest = self.q.popleft()
            if newest is not None:
                with S.lock:
                    S.jpeg = newest[1]


def _run_camera(det, cam, frame_interval, cfg, grab, pub):
    cam_id = cam["id"]
    db.health(cam_id, "up", "stream opened")
    with S.lock:
        S.cam_id, S.cam_label, S.live = cam_id, cam.get("label", cam_id), True
        S.ped_total = S.veh_total = S.bike_total = 0
        S.started = time.time()

    ped_tr = CentroidTracker(max_dist=90, ttl=8)
    veh_tr = CentroidTracker(max_dist=130, ttl=8)
    bike_tr = CentroidTracker(max_dist=110, ttl=8)
    ped_cb = ConfirmBook(MIN_TRACK_FRAMES, MIN_MOVE_PX)
    veh_cb = ConfirmBook(MIN_TRACK_FRAMES, MIN_MOVE_PX)
    bike_cb = ConfirmBook(MIN_TRACK_FRAMES, MIN_MOVE_PX)
    bgsub = cv2.createBackgroundSubtractorMOG2(history=400, varThreshold=40,
                                               detectShadows=False)
    cond_streak = 0
    zones = []               # [{id, poly, zone, bbox}] — manual poly + AI crossings
    islands = []             # refuge-island exclusion polygons
    zones_have_scene = False
    scene = load_scene_safe(cam_id)
    m_per_px = None
    speeds = None
    ring = deque()  # (ts, clip_frame)
    ep = None
    scene_try_ts = 0.0
    idle_frames = 0
    t_open = time.time()
    last_cfg_check = time.time()
    dbg_t = 0.0
    dbg_iters = 0
    cts_prev = None      # content time of the previously processed frame
    due_cts = 0.0        # next content time we want a frame at
    proc_ema = 1.0 / TARGET_FPS   # EMA of per-frame processing cost (adaptive stride)
    zone_cooldown = {}   # zone_id -> content time until which new episodes are muted
    speed_streak = {}    # veh tid -> consecutive over-limit samples
    speed_flagged = set()  # veh tids already reported this pass-through
    scene_mtime = 0.0
    scene_mtime_check = 0.0
    try:
        scene_mtime = os.path.getmtime(scene_path(cam_id))
    except OSError:
        pass

    def build_zones(w, h):
        """Event zones: the manually configured crossing PLUS every pedestrian
        crossing AND cyclist crossing the AI scene-context found — so ALL
        painted crossings in view are monitored. Refuge islands come back as
        EXCLUSION polygons (a person standing there is not on any crossing)."""
        # When an AI/admin zone map exists, it is the ONLY source of live zones
        # — otherwise the editor shows one thing and the stream another (the
        # bootstrap polygon from cameras.json haunted the overlay). The manual
        # polygon is just the fallback for an uncalibrated camera.
        zs = []
        scene_has_zones = isinstance(scene, dict) and bool(
            (scene.get("crossings") or []) + (scene.get("bike_crossings") or []))
        if not scene_has_zones:
            raw = cam.get("poly") or _default_cams()["cameras"][0]["poly"]
            mp = [(p[0] * w, p[1] * h) for p in raw
                  if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(mp) >= 3:   # a degenerate poly must never crash the loop
                zs.append({"id": "main", "kind": "ped", "poly": mp,
                           "zone": PolygonZone(mp), "bbox": poly_bbox(mp, w, h)})
        isl = []
        if isinstance(scene, dict):
            def add(items, prefix, kind):
                for c in (items or []):
                    if not isinstance(c, dict):
                        continue
                    pts = [(p[0] * w, p[1] * h) for p in (c.get("polygon") or [])
                           if isinstance(p, (list, tuple)) and len(p) >= 2]
                    if len(pts) < 3:
                        continue
                    bb = poly_bbox(pts, w, h)
                    if any(_iou(bb, z["bbox"]) > 0.45 for z in zs):
                        continue  # same crossing as an existing zone
                    zs.append({"id": str(c.get("id", f"{prefix}{len(zs)}")),
                               "kind": kind, "poly": pts,
                               "zone": PolygonZone(pts), "bbox": bb})
            add(scene.get("crossings"), "cx", "ped")
            add(scene.get("bike_crossings"), "bx", "bike")
            for c in (scene.get("islands") or []):
                if not isinstance(c, dict):
                    continue
                pts = [(p[0] * w, p[1] * h) for p in (c.get("polygon") or [])
                       if isinstance(p, (list, tuple)) and len(p) >= 2]
                if len(pts) < 3:
                    continue
                # sanity: one hallucinated island covering the crossing would
                # mute EVERY event on the camera. Cap its size and require it
                # to actually touch some crossing.
                bb = poly_bbox(pts, w, h, margin=0)
                barea = (bb[2] - bb[0]) * (bb[3] - bb[1])
                if barea > 0.08 * w * h:
                    print(f"scene island {c.get('id')} rejected: too large", flush=True)
                    continue
                if not any(_iou(bb, z["bbox"]) > 0.01 for z in zs):
                    print(f"scene island {c.get('id')} rejected: touches no crossing",
                          flush=True)
                    continue
                isl.append(PolygonZone(pts))
        return zs[:8], isl

    while True:
        now = time.time()
        dbg_iters += 1
        if now - dbg_t > 15:
            dbg_t = now
            print(f"dbg iters15s={dbg_iters} reads={grab.reads} rfails={grab.fails} "
                  f"q={grab.qlen()} idle={idle_frames} stride={proc_ema:.2f} "
                  f"fps={S.fps:.2f}", flush=True)
            dbg_iters = 0
            grab.reads = 0
            grab.fails = 0
        if grab.t == 0.0:                       # nothing decoded yet
            if now - t_open > 30:
                grab.close()
                return False
            time.sleep(0.1)
            continue
        if now - grab.t > 30:                   # reader starved -> stream died
            grab.close()
            db.health(cam_id, "down", "stream stalled")
            return False
        # EVEN sampling in content time: stride adapts to what the CPU can
        # actually sustain, so processed frames are uniformly spaced — steady
        # fps instead of bursts, and speed estimates stay correct.
        item = grab.next_due(due_cts)
        if item is None:                        # queue empty / next frame not due
            time.sleep(0.03)
            continue
        cts, frame = item
        stride = max(frame_interval, proc_ema * 1.15)
        due_cts = cts + stride
        dt = (cts - cts_prev) if cts_prev is not None else stride
        cts_prev = cts
        t_proc0 = time.time()

        # admin may have switched camera / edited config
        if now - last_cfg_check > 5:
            last_cfg_check = now
            c2 = cams_load()
            if c2.get("active") != cam_id or c2["cameras"] != cfg["cameras"]:
                grab.close()
                db.health(cam_id, "switch", "config changed")
                return True

        h0, w0 = frame.shape[:2]
        if w0 > PROC_W:
            frame = cv2.resize(frame, (PROC_W, int(h0 * PROC_W / w0)))
        else:
            frame = frame.copy()   # never mutate the grabber's buffer in place
        h, w = frame.shape[:2]
        if not zones:
            zones, islands = build_zones(w, h)
            zones_have_scene = bool(scene)
            m_full = cam.get("m_per_px_fullw", 0.075)
            m_per_px = m_full * (1920.0 / w)  # config is per full-width pixel

            autoscale = AutoScale(cam_id, h, make_scale(zones, scene, h, m_per_px))

            def _sfn(y, _m=m_per_px, _a=autoscale):
                v = _a.scale(y)
                return v if v else _m
            speeds = SpeedBook(m_per_px, _sfn)

        # one-time scene context per camera (AI) — run in a BACKGROUND thread so a
        # slow local LLM never blocks the frame loop. Result lands on disk; the
        # main loop reloads it below.
        # a zone map WITHOUT event rules (hand-drawn or legacy) weakens the AI
        # verdicts — fill the rules in automatically, once per 6 h max
        if isinstance(scene, dict) and scene.get("crossings") \
                and not scene.get("event_rules") \
                and now - float(scene.get("_rules_tried", 0)) > 21600 \
                and ai_analyst.enabled() and cam_id not in _scene_busy:
            scene["_rules_tried"] = now
            ok2, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                threading.Thread(target=_rules_worker,
                                 args=(cam_id, jb.tobytes(), dict(scene)),
                                 daemon=True).start()

        recal_flag = os.path.join(SCENE_DIR, f"recal_{cam_id}.flag")
        if scene is None and ai_analyst.enabled() \
                and (now - scene_try_ts > 120 or os.path.exists(recal_flag)):
            scene_try_ts = now
            try:
                os.remove(recal_flag)
            except OSError:
                pass
            ok2, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                threading.Thread(target=_scene_worker, args=(cam_id, jb.tobytes()),
                                 daemon=True).start()
                with S.lock:
                    S.ticker.appendleft("AI kalibruje strefy… (~1-2 min)")
        # hot-reload: the admin zone editor (or AI recalibration) rewrites the
        # scene file — pick it up within ~10 s without a restart
        if now - scene_mtime_check > 10:
            scene_mtime_check = now
            try:
                mt = os.path.getmtime(scene_path(cam_id))
            except OSError:
                mt = 0.0
            if mt != scene_mtime:
                scene_mtime = mt
                scene = load_scene_safe(cam_id)
                zones, islands = build_zones(w, h)
                zones_have_scene = bool(scene)
                if speeds is not None:
                    autoscale.fallback = make_scale(zones, scene, h, m_per_px)
                if scene:
                    with S.lock:
                        S.ticker.appendleft("Strefy przejść zaktualizowane ✔")
        if scene and not zones_have_scene:
            zones, islands = build_zones(w, h)  # AI crossings arrived -> monitor all
            zones_have_scene = True

        # foreground (motion) mask for this fixed camera — static objects
        # (poles, signs, traffic lights) produce ~no foreground and get gated out
        fgmask = bgsub.apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), learningRate=0.01)

        # scene completely still for a while -> skip inference entirely this
        # frame (big CPU saving at night / empty street; hysteresis avoids
        # flapping). MOG2 keeps learning so we wake instantly on motion.
        frame_fg = float((fgmask > 0).mean())
        idle_frames = idle_frames + 1 if frame_fg < 0.0004 else 0
        if idle_frames >= 3:
            dets = []
        else:
            dets = det.detect(frame)
            # ROI-upscale pass over the (max 2) zones that show motion NOW —
            # catches small/far pedestrians without paying for still zones
            zx = []
            for z in zones:
                x1, y1, x2, y2 = z["bbox"]
                patch = fgmask[y1:y2, x1:x2]
                zfg = float((patch > 0).mean()) if patch.size else 0.0
                if zfg > 0.002:
                    zx.append((zfg, z))
            zx.sort(key=lambda t: -t[0])
            # ONE ROI pass per frame (the busiest zone): the full-frame pass
            # already covers every zone; a second ROI pass costs ~0.5 s/frame
            # on this CPU and halves the live fps at rush hour.
            for _, z in zx[:1]:
                for r in detect_roi(det, frame, z["bbox"]):
                    if not any(_iou(r.xyxy, d.xyxy) > 0.45 and r.cls == d.cls
                               for d in dets):
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

        # privacy blur (riders get head-blur too). Toggleable: BLUR=0 disables
        # while running on a weak CPU box (measured cost ~9 ms/frame ≈ 2%).
        if BLUR:
            pboxes = [head_region(d.xyxy) for d in dets if d.cls in (PERSON, BIKE)]
            pboxes += [plate_region(d.xyxy) for d in dets if d.cls == VEHICLE]
            frame = blur_regions(frame, pboxes, block=10)

        bikes = [d for d in dets if d.cls == BIKE]
        vehs = [d for d in dets if d.cls == VEHICLE]
        # a person overlapping a MOVING two-wheeler is its RIDER -> one road user,
        # not a pedestrian (kills double counting AND the "motorcyclist / cyclist
        # triggered a pedestrian event on himself" false positives, #486/#510/
        # #517). Two-wheelers = bicycles (BIKE) + MOTORCYCLES (COCO id 3, which
        # aggregate into VEHICLE but still carry a rider, not a pedestrian). A
        # parked two-wheeler shows no foreground, so it never suppresses real
        # pedestrians walking past it. The motorcycle itself stays a VEHICLE that
        # must yield — only its rider is removed from the pedestrian set.
        _moving_two = [b for b in bikes
                       if fg_ratio_at(fgmask, *b.anchor) >= FG_MIN]
        _moving_two += [v for v in vehs
                        if v.coco_id == 3 and fg_ratio_at(fgmask, *v.anchor) >= FG_MIN]
        def _is_rider(d):
            return any(_iou(d.xyxy, b.xyxy) > 0.18 for b in _moving_two)
        # a person box mostly INSIDE a vehicle box AND sitting HIGH in it is
        # the driver/passenger seen through the glass — not a pedestrian.
        # A pedestrian walking IN FRONT of a stopped car also overlaps its box
        # (the exact conflict geometry!) but their feet reach the box bottom —
        # the positional test keeps them alive.
        def _in_vehicle(d):
            x1, y1, x2, y2 = d.xyxy
            area = max(1.0, (x2 - x1) * (y2 - y1))
            for v in vehs:
                ix = max(0.0, min(x2, v.xyxy[2]) - max(x1, v.xyxy[0]))
                iy = max(0.0, min(y2, v.xyxy[3]) - max(y1, v.xyxy[1]))
                if ix * iy / area > 0.6:
                    vh = max(1.0, v.xyxy[3] - v.xyxy[1])
                    if (y2 - v.xyxy[1]) / vh < 0.72:  # feet-line high = occupant
                        return True
            return False
        peds = [d for d in dets if d.cls == PERSON
                and not _is_rider(d) and not _in_vehicle(d)]
        # A "vehicle" box that sits ON a pedestrian and is not much larger than
        # them is almost certainly that person's PRAM / WHEELCHAIR / shopping
        # cart misread as a car — never a real motor vehicle. Drop it, so a lone
        # pedestrian pushing a stroller cannot fabricate a "vehicle in zone" and
        # trigger a false failed-to-yield episode with no car present. A real car
        # is many times larger than a person, so it is never dropped — even when
        # a pedestrian walks right in front of it (the true conflict geometry).
        def _is_ped_object(v):
            vx1, vy1, vx2, vy2 = v.xyxy
            va = max(1.0, (vx2 - vx1) * (vy2 - vy1))
            for d in peds:
                x1, y1, x2, y2 = d.xyxy
                pa = max(1.0, (x2 - x1) * (y2 - y1))
                ix = max(0.0, min(vx2, x2) - max(vx1, x1))
                iy = max(0.0, min(vy2, y2) - max(vy1, y1))
                if ix * iy / va > 0.5 and va < 2.5 * pa:
                    return True
            return False
        vehs = [v for v in vehs if not _is_ped_object(v)]
        ped_tracks = ped_tr.update(peds)
        veh_tracks = veh_tr.update(vehs)
        bike_tracks = bike_tr.update(bikes)

        # which tracks show foreground motion right now
        ped_fg = {tid for tid, p in ped_tracks.items() if fg_ratio_at(fgmask, *p) >= FG_MIN}
        veh_fg = {tid for tid, p in veh_tracks.items() if fg_ratio_at(fgmask, *p) >= FG_MIN}
        bike_fg = {tid for tid, p in bike_tracks.items() if fg_ratio_at(fgmask, *p) >= FG_MIN}
        # confirm real objects (moved over lifetime) — count ONLY on confirmation
        new_ped = len(ped_cb.update(ped_tracks, ped_fg, cts))
        new_veh = len(veh_cb.update(veh_tracks, veh_fg, cts))
        new_bike = len(bike_cb.update(bike_tracks, bike_fg, cts))
        if new_ped or new_veh or new_bike:
            db.bump_counts(cam_id, new_ped, new_veh, 0, bike=new_bike)
        db.bump_counts(cam_id, 0, 0, min(dt, 3.0))

        # kinematics on CONTENT time — immune to processing jitter
        veh_kmh = speeds.update(veh_tracks, cts)
        if not hasattr(speeds, "_pedbook"):
            speeds._pedbook = SpeedBook(m_per_px, speeds.scale_fn)
            speeds._bikebook = SpeedBook(m_per_px, speeds.scale_fn)
        speeds._pedbook.walker = True
        speeds._bikebook.walker = True
        # per-track pedestrian box height (the tracker's position is the matched
        # detection's bottom-centre, so nearest-anchor recovers its box) — feeds
        # the person's-own-height ruler in SpeedBook so speeds are distance-correct
        # even with no camera calibration.
        ped_heights = {}
        for tid, pos in ped_tracks.items():
            best_h, best_d = 0.0, 1e9
            for d in peds:
                dd = (d.anchor[0] - pos[0]) ** 2 + (d.anchor[1] - pos[1]) ** 2
                if dd < best_d:
                    best_d, best_h = dd, (d.xyxy[3] - d.xyxy[1])
            if best_d < 25.0:
                ped_heights[tid] = best_h
        ped_kmh = speeds._pedbook.update(ped_tracks, cts, heights=ped_heights)
        bike_kmh = speeds._bikebook.update(bike_tracks, cts)
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

        # ---- event kinematics, checked in EVERY monitored crossing ----
        # A conflict needs BOTH sides in the SAME zone at the same time:
        #   pedestrian: confirmed track, WALKING (not standing on an island), and
        #   vehicle: confirmed track, MOVING through (a car politely stopped at
        #   the zebra/red light is stationary -> never triggers).
        pedbook = speeds._pedbook
        bikebook = speeds._bikebook

        def on_island(p):
            # a person on a refuge island has LEFT the crossing half behind —
            # cars on that half no longer conflict with them (the silver-car
            # false-violation case)
            return any(i.contains(p) for i in islands)

        # A cyclist whose bicycle flickers between the BIKE and the MOTORCYCLE
        # (=VEHICLE) class from frame to frame spawns BOTH a bike track and a
        # phantom "vehicle" track at the SAME spot — the cyclist then conflicts
        # with HIMSELF and fires a bogus "failed to yield" event with no real
        # car present (events #505, #513). Suppress any vehicle track sitting on
        # top of a bike track: a genuine motor vehicle is never co-located with
        # a separately-tracked bicycle, and a real motorcycle produces NO
        # parallel BIKE detection, so neither is ever wrongly dropped.
        # Threshold kept TIGHT: the phantom is the SAME object, so its anchor
        # nearly coincides with the bike's; a real car beside a cyclist sits
        # farther away, so a genuine car-vs-cyclist conflict is NOT suppressed.
        _dedup_px = 0.04 * w
        _bike_pts = list(bike_tracks.values())
        _phantom_veh = {tid for tid, vp in veh_tracks.items()
                        if any(abs(vp[0] - bx) < _dedup_px and abs(vp[1] - by) < _dedup_px
                               for bx, by in _bike_pts)}

        ped_in, veh_in_moving, hit_zone = [], [], None
        mark_ped, mark_bike = set(), set()   # participant tids (for red marking)
        for z in zones:
            if zone_cooldown.get(z["id"], 0) > cts:
                continue   # fresh episode just ended here — mute duplicates
            zcx = sum(px for px, _ in z["poly"]) / len(z["poly"])
            zcy = sum(py for _, py in z["poly"]) / len(z["poly"])
            is_bike_zone = z.get("kind") == "bike"
            # match the vulnerable user to the zone TYPE: a pedestrian crossing
            # (zebra) conflicts only with PEDESTRIANS, a cyclist crossing only
            # with CYCLISTS. Bike crossings sit right next to zebras, so a
            # cyclist riding across his OWN lane used to fall inside the zebra
            # polygon and fire a false pedestrian-conflict — this split fixes it.
            if is_bike_zone:
                pz_p = []
                pz_b = [tid for tid, p in bike_tracks.items()
                        if z["zone"].contains(p) and not on_island(p)
                        and bike_cb.confirmed(tid) and bikebook.is_moving(tid)
                        and bikebook.heading_ok(tid, zcx, zcy)]
            else:
                pz_p = [tid for tid, p in ped_tracks.items()
                        if z["zone"].contains(p) and not on_island(p)
                        and ped_cb.confirmed(tid) and pedbook.is_moving(tid)]
                pz_b = []
            # vehicle must be INSIDE this crossing polygon (not its wide bbox)
            # AND driving TOWARD it — a car on its own lane that never crosses
            # THIS zone, already passed it, or turns away is not a conflict
            vz = [tid for tid, p in veh_tracks.items()
                  if z["zone"].contains(p) and veh_cb.confirmed(tid)
                  and tid not in _phantom_veh
                  and speeds.is_moving(tid) and speeds.heading_ok(tid, zcx, zcy)]
            users = len(pz_p) + len(pz_b)
            if users and vz and users + len(vz) > len(ped_in) + len(veh_in_moving):
                ped_in = pz_p + pz_b
                veh_in_moving, hit_zone = vz, z["id"]
                mark_ped, mark_bike = set(pz_p), set(pz_b)
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
        active_bikes = [d for d in bikes if det_active(d)]
        # every moving person/car doubles as a measuring stick — the speed
        # scale self-calibrates per image row, no manual steps on any camera
        autoscale.feed(active_peds, active_vehs)

        def _tid_near(d, tracks):
            ax, ay = d.anchor
            best, bd = None, 45.0
            for tid, (tx, ty) in tracks.items():
                dd = ((ax - tx) ** 2 + (ay - ty) ** 2) ** 0.5
                if dd < bd:
                    best, bd = tid, dd
            return best

        # annotate — ONE color per zone TYPE (matches the admin editor legend):
        # yellow = pedestrian crossings (incl. the manual one), cyan = bike
        # crossings, magenta = refuge islands (exclusion areas)
        ann = frame.copy()
        for z in zones:
            col = (60, 200, 255) if z.get("kind") != "bike" else (235, 235, 60)
            cv2.polylines(ann, [np.array(z["poly"], np.int32)], True, col, 2)
        for i in islands:
            cv2.polylines(ann, [np.array(i.poly, np.int32)], True,
                          (255, 0, 255), 2)
        # traffic-light heads (admin-drawn bbox) with the colour we read from
        # them by HSV — so the operator sees the signal state on the stream
        if isinstance(scene, dict):
            _tlcol = {"red": (0, 0, 235), "green": (60, 200, 60),
                      "amber": (0, 180, 235), "unknown": (150, 150, 150)}
            for t in (scene.get("traffic_lights") or []):
                b = t.get("bbox") if isinstance(t, dict) else None
                if isinstance(b, (list, tuple)) and len(b) == 4:
                    x1, y1 = int(b[0] * w), int(b[1] * h)
                    x2, y2 = int(b[2] * w), int(b[3] * h)
                    st = tl_states.get(t.get("id", "tl"), "unknown")
                    cv2.rectangle(ann, (x1, y1), (x2, y2), _tlcol.get(st, (150, 150, 150)), 2)
        RED = (40, 40, 255)
        for d, tracks, book, col, mk, tag in (
                [(d, ped_tracks, ped_kmh, (90, 230, 120), mark_ped, "KONFLIKT")
                 for d in active_peds] +
                [(d, veh_tracks, veh_kmh, (80, 150, 255), set(veh_in_moving),
                  "NIE USTĄPIŁ?") for d in active_vehs] +
                [(d, bike_tracks, bike_kmh, (60, 220, 235), mark_bike, "KONFLIKT")
                 for d in active_bikes]):
            x1, y1, x2, y2 = [int(v) for v in d.xyxy]
            tid = _tid_near(d, tracks)
            v = book.get(tid) if tid is not None else None
            flag = tid is not None and instant_condition and tid in mk
            speed_flag = (tag == "NIE USTĄPIŁ?" and tid in speed_flagged)
            if flag or speed_flag:
                # the (potential) violator/participant — unmissable in the live
                # view AND in the recorded clip, with the reason on the box
                cv2.rectangle(ann, (x1, y1), (x2, y2), RED, 3)
                label = "PREDKOSC!" if speed_flag and not flag else tag
                if v is not None and v > 1.5:
                    label += f" ~{v:.0f}km/h"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                cv2.rectangle(ann, (x1, max(0, y1 - th - 10)),
                              (x1 + tw + 8, max(th + 10, y1)), RED, -1)
                cv2.putText(ann, label, (x1 + 4, max(th + 2, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2,
                            cv2.LINE_AA)
            else:
                cv2.rectangle(ann, (x1, y1), (x2, y2), col, 2)
                if v is not None and v > 1.5:
                    cv2.putText(ann, f"{v:.0f} km/h", (x1, max(12, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.46, col, 1, cv2.LINE_AA)
        ann[:44] = (ann[:44] * 0.35).astype(np.uint8)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cv2.circle(ann, (20, 22), 7, (60, 60, 235), -1)
        vmax = max(veh_kmh.values()) if veh_kmh else 0
        cv2.putText(ann, f"LIVE | piesi: {len(active_peds)}  rowery: {len(active_bikes)}"
                         f"  pojazdy: {len(active_vehs)}"
                         f" | max ~{vmax:.0f} km/h | sygnalizacja: {tl_summary[:24]} | {ts}",
                    (36, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (240, 240, 240), 1, cv2.LINE_AA)
        if condition or (ep is not None):
            cv2.rectangle(ann, (0, 0), (ann.shape[1] - 1, ann.shape[0] - 1), (0, 0, 235), 5)

        # clip frame for ring buffer / episode — CONTENT time, so clips play
        # back smoothly at their true rate
        small = cv2.resize(ann, (CLIP_W, int(ann.shape[0] * CLIP_W / ann.shape[1])))
        ring.append((cts, small))
        while ring and cts - ring[0][0] > PRE_ROLL_S:
            if ep is None:
                ring.popleft()
            else:
                break

        # ---- speeding detection (rough monocular estimate, flagged only well
        # above the limit; each event gets a clip + AI sanity check + votes) ----
        for tid, v in veh_kmh.items():
            if v >= SPEED_LIMIT_KMH * SPEED_FLAG_FACTOR and veh_cb.confirmed(tid) \
                    and veh_cb.info.get(tid, {}).get("n", 0) >= 6:
                speed_streak[tid] = speed_streak.get(tid, 0) + 1
                if speed_streak[tid] >= 3 and tid not in speed_flagged \
                        and S.recording_ok and ep is None:
                    speed_flagged.add(tid)
                    keepf = list(ring)
                    if len(keepf) > 3:
                        stamp = int(cts)
                        cname = f"sp_{cam_id}_{stamp}_{tid}.mp4"
                        sname = f"sp_{cam_id}_{stamp}_{tid}.jpg"
                        okc = write_clip(os.path.join(CLIP_DIR, cname), keepf)
                        cv2.imwrite(os.path.join(SNAP_DIR, sname), ann,
                                    [cv2.IMWRITE_JPEG_QUALITY, 80])
                        desc = (f"Możliwe przekroczenie prędkości: ~{v:.0f} km/h "
                                f"(limit {SPEED_LIMIT_KMH:.0f}; pomiar szacunkowy "
                                f"monokularowy ±30% — orientacyjny, to nie radar).")
                        eid = db.add_event(cam_id, desc, sname,
                                           cname if okc else None,
                                           max(MIN_CLIP_SEC, keepf[-1][0] - keepf[0][0]),
                                           tl_summary[:24], round(v, 1), 0, 1,
                                           kind="speeding")
                        with S.lock:
                            S.ticker.appendleft(f"#{eid} ~{v:.0f} km/h → weryfikacja")
            else:
                speed_streak.pop(tid, None)
        for tid in list(speed_flagged):
            if tid not in veh_tracks:
                speed_flagged.discard(tid)

        # ---- episode state machine ----
        if ep is None and condition and S.recording_ok:
            ep = Episode()
            ep.start = cts
            ep.last_cond = cts
            ep.frames = list(ring)
            ep.zone_id = hit_zone or "main"
            with S.lock:
                S.episode_active = True
        if ep is not None:
            ep.frames.append((cts, small))
            for pref, trks in (("p", ped_tracks), ("v", veh_tracks), ("b", bike_tracks)):
                for tid, (tx, ty) in trks.items():
                    q = ep.paths.setdefault(f"{pref}{tid}", [])
                    if len(q) < 60:
                        q.append([round(cts - ep.start, 1),
                                  round(tx / w, 3), round(ty / h, 3)])
            if condition:
                ep.last_cond = cts
                ep.had_bike = getattr(ep, "had_bike", False) or bool(mark_bike)
                # speed of the CONFLICTING vehicle(s), not the frame-wide max
                vz_max = max((veh_kmh.get(t, 0.0) for t in veh_in_moving),
                             default=0.0)
                ep.max_kmh = max(ep.max_kmh, vz_max)
                ep.n_ped = max(ep.n_ped, len(ped_in))
                ep.n_veh = max(ep.n_veh, len(veh_in_moving))
                ep.tl_states.append(tl_summary)
                overlap = len(ped_in) + len(veh_in_moving)
                if overlap > ep.best_overlap:
                    ep.best_overlap = overlap
                    ep.best_snap = ann.copy()
            ended_lapse = cts - ep.last_cond > EPISODE_END_S
            ended_max = cts - ep.start > EPISODE_MAX_S
            if ended_lapse or ended_max:
                # ALWAYS save (a >35 s sustained conflict is the most
                # report-worthy case — force-finalize, never discard)
                if True:
                    keep = ([f for f in ep.frames if f[0] <= ep.last_cond + POST_ROLL_S]
                            if ended_lapse else list(ep.frames))
                    # honest duration = span of the recorded clip (never 0)
                    dur = max(MIN_CLIP_SEC, (keep[-1][0] - keep[0][0]) if len(keep) > 1 else 0.0)
                    stamp = int(ep.start)
                    clip_name = f"ep_{cam_id}_{stamp}.mp4"
                    snap_name = f"ep_{cam_id}_{stamp}.jpg"
                    ok_clip = write_clip(os.path.join(CLIP_DIR, clip_name), keep)
                    if ep.best_snap is not None:
                        cv2.imwrite(os.path.join(SNAP_DIR, snap_name), ep.best_snap,
                                    [cv2.IMWRITE_JPEG_QUALITY, 80])
                    tl_mode = max(set(ep.tl_states), key=ep.tl_states.count) if ep.tl_states else "unknown"
                    zlbl = getattr(ep, "zone_id", "main")
                    desc = (f"Pojazd w ruchu i idący pieszy jednocześnie na przejściu "
                            f"[strefa: {zlbl}] (piesi: {ep.n_ped}, pojazdy w ruchu: {ep.n_veh}, "
                            f"max ~{ep.max_kmh:.0f} km/h, sygnalizacja: {tl_mode}).")
                    eid = db.add_event(cam_id, desc, snap_name,
                                       clip_name if ok_clip else None,
                                       dur, tl_mode, round(ep.max_kmh, 1),
                                       ep.n_ped, ep.n_veh,
                                       tracks_json=ep.paths_json(),
                                       flags={"bike": True}
                                       if getattr(ep, "had_bike", False) else None)
                    with S.lock:
                        S.ticker.appendleft(f"#{eid} epizod zapisany ({dur:.0f}s) → analiza AI…")
                # mute this zone briefly: a busy crossing otherwise spawns a
                # near-duplicate episode every few seconds and burns AI budget
                zone_cooldown[getattr(ep, "zone_id", "main")] = cts + EPISODE_COOLDOWN_S
                ep = None
                with S.lock:
                    S.episode_active = False

        with S.lock:
            S.ped_total += new_ped
            S.veh_total += new_veh
            S.bike_total += new_bike
            S.in_ped, S.in_veh = len(active_peds), len(active_vehs)
            S.in_bike = len(active_bikes)
            S.fps = 0.8 * S.fps + 0.2 * (1.0 / dt if dt > 0 else 0)
            S.last_frame_ts = now
            S.tl = tl_states
            S.speeds_now = {
                "veh_kmh": round(max(veh_kmh.values()), 1) if veh_kmh else None,
                "ped_kmh": round(max(ped_kmh.values()), 1) if ped_kmh else None,
                "bike_kmh": round(max(bike_kmh.values()), 1) if bike_kmh else None}
            live = ann
            if ann.shape[1] > LIVE_WIDTH:
                live = cv2.resize(ann, (LIVE_WIDTH,
                                        int(ann.shape[0] * LIVE_WIDTH / ann.shape[1])))
            ok3, buf = cv2.imencode(".jpg", live, [cv2.IMWRITE_JPEG_QUALITY, 68])
            S.frame_raw = frame       # clean copy for the admin zone editor
            # Persist a per-camera REFERENCE frame (throttled ~20 s) so the zone
            # editor can show ANY camera's real geometry — not only the running
            # one. Without this, editing a non-active camera would draw zones on
            # the wrong picture; with it, every camera is edited on its own view.
            if cts - getattr(S, "_ref_saved", 0) > 20:
                S._ref_saved = cts
                try:
                    cv2.imwrite(os.path.join(SCENE_DIR, f"frame_{cam_id}.jpg"),
                                frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                except Exception:
                    pass
        if ok3:
            pub.push(cts, buf.tobytes())   # constant-latency playout, not direct
        # adaptive stride: track what one frame really costs on this CPU
        proc_ema = 0.8 * proc_ema + 0.2 * (time.time() - t_proc0)


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
            fresh = (time.time() - S.last_frame_ts) < 60
            return self._json(200 if fresh else 503,
                              {"ok": fresh, "live": S.live and fresh,
                               "frame_age_s": round(time.time() - S.last_frame_ts, 1)
                               if S.last_frame_ts else None})
        if p == "/state.json":
            with S.lock:
                el = max(1e-6, (time.time() - S.started) / 3600.0)
                d = {"live": S.live, "cam_id": S.cam_id, "source": S.cam_label,
                     "ped_total": S.ped_total, "veh_total": S.veh_total,
                     "bike_total": S.bike_total,
                     "ped_per_hour": round(S.ped_total / el, 1),
                     "veh_per_hour": round(S.veh_total / el, 1),
                     "bike_per_hour": round(S.bike_total / el, 1),
                     "in_frame": {"ped": S.in_ped, "veh": S.in_veh, "bike": S.in_bike},
                     "fps": round(S.fps, 1), "tl": S.tl, "speeds": S.speeds_now,
                     "episode_active": S.episode_active,
                     "recording_ok": S.recording_ok,
                     "ticker": list(S.ticker),
                     "ai_enabled": ai_analyst.enabled(),
                     "ai_calls_today": db.ai_calls_today()}
            d["stats"] = db.stats(d["cam_id"] or None)
            d["events"] = db.list_events("top", 9, cam_id=d["cam_id"] or None)
            # camera playlist countdown for the front page
            try:
                cfg = cams_load()
                pl = cfg.get("playlist") or {}
                cur = next((c for c in cfg["cameras"] if c["id"] == d["cam_id"]), None)
                if cur:
                    d["source_url"] = cur.get("source_url", "")
                ids = {c["id"]: c for c in cfg["cameras"]}
                rota = [c for c in (pl.get("cameras") or []) if c in ids]
                if pl.get("enabled") and len(rota) >= 2:
                    iv = max(1.0, float(pl.get("interval_min", 10))) * 60.0
                    left = max(0, int(float(pl.get("last_switch", 0)) + iv - time.time()))
                    curid = cfg.get("active")
                    nxt = rota[(rota.index(curid) + 1) % len(rota)] \
                        if curid in rota else rota[0]
                    d["playlist"] = {"next_in_s": left,
                                     "next_label": ids[nxt].get("label", nxt)}
            except Exception:
                pass
            return self._json(200, d)
        if p == "/events.json":
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            tab = (q.get("tab") or ["all"])[0]
            offset = int((q.get("offset") or ["0"])[0])
            hour = (q.get("hour") or [None])[0]   # 'YYYY-MM-DDTHH' chart click-filter
            return self._json(200, {"events": db.list_events(tab, 12, offset, hour=hour)})
        if p == "/charts.json":
            cam = S.cam_id or cams_load().get("active", "")
            return self._json(200, db.charts(cam))
        if p == "/api/stats":
            return self._json(200, db.stats())
        if p.startswith("/share/"):
            return self._share_page(p.rsplit("/", 1)[-1])
        if p.startswith("/snap/"):
            return self._file(os.path.join(SNAP_DIR, os.path.basename(p)), "image/jpeg")
        if p.startswith("/clip/"):
            return self._file(os.path.join(CLIP_DIR, os.path.basename(p)), "video/mp4")
        if p == "/scene.json":
            # zone editor may request a SPECIFIC camera (?cam=id); without it we
            # serve the running camera. Scoping read+write by explicit id is what
            # stops one camera's zones bleeding onto another.
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            cam = _known_cam((q.get("cam") or [""])[0]) or S.cam_id or cams_load().get("active", "")
            sc = load_scene(cam)
            if not sc:
                calibrating = (os.path.exists(os.path.join(SCENE_DIR, f"recal_{cam}.flag"))
                               or cam in _scene_busy)
                return self._json(404, {"ok": False, "cam": cam,
                                        "calibrating": calibrating,
                                        "ai_available": ai_analyst.enabled(),
                                        "ai_calls_today": db.ai_calls_today()})
            sc["cam"] = cam
            return self._json(200, sc)
        if p in ("/frame.jpg", "/frame_raw.jpg"):
            # /frame_raw.jpg = CLEAN frame (no zones/boxes) — the zone editor
            # must never show baked-in overlays that look like editable zones.
            # ?cam=id lets the editor show a NON-running camera's LAST reference
            # frame (persisted periodically), so zones are always drawn on the
            # correct camera's geometry — never the active camera's picture.
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            req_cam = _known_cam((q.get("cam") or [""])[0])
            if (p == "/frame_raw.jpg" and req_cam and req_cam != (S.cam_id or "")):
                ref = os.path.join(SCENE_DIR, f"frame_{req_cam}.jpg")
                if os.path.exists(ref):
                    try:
                        buf = open(ref, "rb").read()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(buf)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return self.wfile.write(buf)
                    except OSError:
                        pass
            with S.lock:
                if p == "/frame_raw.jpg" and S.frame_raw is not None:
                    ok9, b9 = cv2.imencode(".jpg", S.frame_raw,
                                           [cv2.IMWRITE_JPEG_QUALITY, 82])
                    buf = b9.tobytes() if ok9 else S.jpeg
                else:
                    buf = S.jpeg
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(buf)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(buf)
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
        if p == "/admin/export":
            # config authority endpoint: cameras + all zone maps, consumed by
            # secondary nodes' config_sync_loop
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            scenes = {}
            try:
                for f in os.listdir(SCENE_DIR):
                    if f.startswith("scene_") and f.endswith(".json"):
                        cid = f[len("scene_"):-len(".json")]
                        try:
                            scenes[cid] = json.load(open(
                                os.path.join(SCENE_DIR, f), encoding="utf-8"))
                        except (OSError, ValueError):
                            pass
            except OSError:
                pass
            return self._json(200, {"cameras": cams_load(), "scenes": scenes,
                                    "global_rules": load_global_rules()})
        if p == "/admin/health":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            return self._json(200, db.camera_uptime_stats())
        if p == "/admin/global-rules":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            return self._json(200, {"ok": True, "rules": load_global_rules()})
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
        if p == "/admin/event":
            # admin bins / restores an event immediately (no crowd vote needed)
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            try:
                eid = int(data.get("id", 0))
            except (ValueError, TypeError):
                return self._json(400, {"ok": False})
            db.set_trashed(eid, 0 if data.get("restore") else 1)
            return self._json(200, {"ok": True})
        if p == "/admin/global-rules":
            # the universal ruleset the AI reads for EVERY event on EVERY camera.
            # Written on the config AUTHORITY; syncs to the other node like scenes.
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            rules = data.get("rules")
            if not isinstance(rules, str) or not rules.strip():
                return self._json(400, {"ok": False, "error": "empty rules"})
            save_global_rules(rules.strip())
            return self._json(200, {"ok": True})
        if p == "/admin/scene":
            # admin saves a hand-corrected zone map (the AI draft is editable:
            # move/add/delete polygons, edit event rules) — validated, then the
            # worker hot-reloads it via the scene-file mtime watch
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            sc = data.get("scene")
            valid = _valid_scene(sc) if isinstance(sc, dict) else None
            if not valid:
                return self._json(400, {"ok": False, "error": "scene failed validation"})
            # write to the EXPLICIT camera the editor loaded (data.cam), never the
            # implicitly-active one — this is the core fix for zones bleeding
            # between cameras when the active camera rotates under the editor.
            cam = _known_cam(data.get("cam")) or S.cam_id or cams_load().get("active", "")
            json.dump(sc, open(scene_path(cam), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            return self._json(200, {"ok": True, "cam": cam})
        if p == "/admin/scene/rules":
            # fill/refresh the AI event rules for the active camera NOW, without
            # touching the hand-drawn polygons
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            cam = _known_cam(data.get("cam")) or S.cam_id or cams_load().get("active", "")
            sc = load_scene(cam)
            if not (isinstance(sc, dict) and sc.get("crossings")):
                return self._json(400, {"ok": False, "error": "no zone map yet"})
            with S.lock:
                jpg = S.frame_raw if cam == (S.cam_id or "") else None
            if jpg is None:
                ref = os.path.join(SCENE_DIR, f"frame_{cam}.jpg")
                jpg = cv2.imread(ref) if os.path.exists(ref) else None
            if jpg is None:
                jpg = None
            else:
                ok9, b9 = cv2.imencode(".jpg", jpg, [cv2.IMWRITE_JPEG_QUALITY, 82])
                jpg = b9.tobytes() if ok9 else None
            if jpg is None:
                return self._json(503, {"ok": False, "error": "no frame"})
            sc2 = dict(sc)
            sc2["event_rules"] = ""      # force regeneration
            threading.Thread(target=_rules_worker, args=(cam, jpg, sc2),
                             kwargs={"force": True}, daemon=True).start()
            return self._json(200, {"ok": True, "cam": cam})
        if p == "/admin/scene/recalibrate":
            # wipe the scene -> the worker re-runs the AI calibration
            # (grid + self-check) on the next frames
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            cam = _known_cam(data.get("cam")) or S.cam_id or cams_load().get("active", "")
            try:
                os.remove(scene_path(cam))
            except OSError:
                pass
            # flag makes the worker start calibration IMMEDIATELY (no 120 s
            # debounce) and lets the editor show a "calibrating…" status
            try:
                open(os.path.join(SCENE_DIR, f"recal_{cam}.flag"), "w").write("1")
            except OSError:
                pass
            return self._json(200, {"ok": True, "cam": cam})
        if p == "/admin/cameras":
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            cfg = cams_load()
            cam = data.get("camera")
            if data.get("delete"):
                cfg["cameras"] = [c for c in cfg["cameras"] if c["id"] != data["delete"]]
            elif cam and cam.get("id") and cam.get("url"):
                dp = [[0.3, 0.6], [0.7, 0.6], [0.7, 0.85], [0.3, 0.85]]
                poly = cam.get("poly")
                if not (isinstance(poly, list) and len(poly) >= 3):
                    cam["poly"] = dp   # a degenerate poly would crash the loop
                cam.setdefault("m_per_px_fullw", 0.075)
                others = [c for c in cfg["cameras"] if c["id"] != cam["id"]]
                cfg["cameras"] = others + [cam]
            if data.get("active"):
                cfg["active"] = data["active"]
            if isinstance(data.get("playlist"), dict):
                p = data["playlist"]
                cfg["playlist"] = {
                    "enabled": bool(p.get("enabled")),
                    "interval_min": max(1.0, float(p.get("interval_min", 10) or 10)),
                    "cameras": [str(x) for x in (p.get("cameras") or [])][:20],
                    "last_switch": float((cfg.get("playlist") or {})
                                         .get("last_switch", 0)),
                }
            cams_save(cfg)
            return self._json(200, cfg)
        return self._json(404, {"ok": False})

    def _share_page(self, sid):
        """Public share card for one event — Open Graph / Twitter meta so a
        shared link renders with the event snapshot, the AI verdict and a clear
        one-line pitch of the project (GEO/SEO)."""
        try:
            ev = db.get_event(int(sid))
        except (ValueError, TypeError):
            ev = None
        base = PUBLIC_BASE
        if not ev:
            self.send_response(302)
            self.send_header("Location", base + "/#live")
            self.end_headers()
            return
        img = f"{base}/cv/snap/{ev['snap']}" if ev.get("snap") else base + "/assets/og.jpg"
        vmap = {"violation": "AI: naruszenie / violation",
                "no_violation": "AI: brak naruszenia / no violation",
                "uncertain": "AI: niepewne / uncertain"}
        verdict = vmap.get(ev.get("ai_verdict") or "", "oczekuje / pending")
        desc_pl = ev.get("ai_pl") or ev.get("desc") or ""
        desc_en = ev.get("ai_en") or ""
        title = f"Bezpieczne Przejścia — zdarzenie #{ev['id']} · {verdict}"
        og_desc = (desc_en or desc_pl or
                   "Live AI watching a real Polish pedestrian crossing — verify the verdict yourself.")[:280]

        def esc(s):
            return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))
        clip_html = ""
        if ev.get("clip"):
            clip_html = (f'<video controls autoplay muted playsinline poster="{esc(img)}" '
                         f'src="{base}/cv/clip/{esc(ev["clip"])}" '
                         'style="width:100%;border-radius:12px"></video>')
        else:
            clip_html = f'<img src="{esc(img)}" style="width:100%;border-radius:12px">'
        html = f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(og_desc)}">
<meta property="og:type" content="video.other">
<meta property="og:site_name" content="Bezpieczne Przejścia / SafeCross">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(og_desc)}">
<meta property="og:image" content="{esc(img)}">
<meta property="og:url" content="{base}/cv/share/{ev['id']}">
{f'<meta property="og:video" content="{base}/cv/clip/{esc(ev["clip"])}">' if ev.get('clip') else ''}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(og_desc)}">
<meta name="twitter:image" content="{esc(img)}">
<style>body{{font:16px/1.6 system-ui;background:#0a0e14;color:#e6edf3;margin:0;padding:1.4rem;max-width:760px;margin:0 auto}}
a.btn{{display:inline-block;background:#2ee6a6;color:#04120c;font-weight:700;padding:.7rem 1.2rem;border-radius:10px;text-decoration:none;margin-top:1rem}}
.v{{display:inline-block;background:#3a1116;color:#ff8a97;border:1px solid #7a2230;border-radius:8px;padding:.15rem .5rem;font-size:.85rem;font-weight:700}}
.mut{{color:#8b97a7}}</style></head><body>
<p><a href="{base}/" style="color:#2ee6a6;font-weight:700;text-decoration:none">◆ Bezpieczne Przejścia / SafeCross</a></p>
<h1 style="font-size:1.3rem">Zdarzenie #{ev['id']} na przejściu dla pieszych</h1>
<p><span class="v">{esc(verdict)}</span> <span class="mut">· {esc((ev.get('ts') or '').replace('T',' ')[:16])} · ~{ev.get('kmh') or '?'} km/h</span></p>
{clip_html}
<p>{esc(desc_pl)}</p>
<p class="mut">{esc(desc_en)}</p>
<p class="mut">AI na żywo analizuje prawdziwe przejście dla pieszych w Polsce, a ludzie weryfikują każdy werdykt. Kod otwarty (Apache-2.0).</p>
<a class="btn" href="{base}/#live">Zobacz kamerę na żywo → / Watch live</a>
<a class="btn" style="background:#1a2432;color:#e6edf3" href="https://github.com/AndriiShramko/bezpieczne-przejscia">Kod na GitHub</a>
</body></html>"""
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(b)

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
                time.sleep(1.0 / max(1.0, LIVE_FPS))
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
    if CONFIG_SYNC_URL:
        threading.Thread(target=config_sync_loop, daemon=True).start()
    else:
        # camera playlist rotates only on the config authority
        threading.Thread(target=playlist_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"cv-service v2 on :{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
