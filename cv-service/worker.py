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
EPISODE_COOLDOWN_S = float(os.environ.get("EPISODE_COOLDOWN_S", "20"))
SPEED_LIMIT_KMH = float(os.environ.get("SPEED_LIMIT_KMH", "50"))
# monocular speed is ±30%: flag only well above the limit to avoid slander
SPEED_FLAG_FACTOR = float(os.environ.get("SPEED_FLAG_FACTOR", "1.35"))
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
                if dt > 0.2:  # never divide by a degenerate window
                    d_px = float(((x - x0) ** 2 + (y - y0) ** 2) ** 0.5)
                    out[tid] = float((d_px / dt) * self.m_per_px * 3.6)  # km/h
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
    if not frames:
        return False
    if fps is None:
        # honest playback rate: frames are evenly spaced in content time, so
        # fps derived from their timestamps makes the clip play in real time
        span = frames[-1][0] - frames[0][0]
        fps = (len(frames) - 1) / span if span > 0.2 and len(frames) > 2 else CLIP_FPS
        fps = max(1.0, min(12.0, fps))
    h, w = frames[0][1].shape[:2]
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for _, f in frames:
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
                    if len(fps_win) > 30 and now - fps_win[0][0] >= 12.0:
                        est = (self.seq - fps_win[0][1]) / (now - fps_win[0][0])
                        if 1.0 <= est <= 120.0 and abs(est - self.src_fps) / self.src_fps > 0.12:
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
                                           trajectories=traj)
            if res:
                db.set_ai_result(eid, res["verdict"], res.get("violator", "none"),
                                 res.get("explanation_pl", ""), res.get("explanation_en", ""),
                                 float(res.get("confidence", 0)))
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
    try:
        return _run_camera(det, cam, frame_interval, cfg, grab)
    finally:
        grab.close()          # idempotent; guarantees no Grabber leak on ANY exit
        with S.lock:
            S.live = False


def _run_camera(det, cam, frame_interval, cfg, grab):
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
        zs = []
        mp = [(p[0] * w, p[1] * h) for p in cam["poly"]]
        zs.append({"id": "main", "poly": mp, "zone": PolygonZone(mp),
                   "bbox": poly_bbox(mp, w, h)})
        isl = []
        if isinstance(scene, dict):
            def add(items, prefix):
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
                               "poly": pts, "zone": PolygonZone(pts), "bbox": bb})
            add(scene.get("crossings"), "cx")
            add(scene.get("bike_crossings"), "bx")
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
            speeds = SpeedBook(m_per_px)

        # one-time scene context per camera (AI) — run in a BACKGROUND thread so a
        # slow local LLM never blocks the frame loop. Result lands on disk; the
        # main loop reloads it below.
        if scene is None and now - scene_try_ts > 120 and ai_analyst.enabled():
            scene_try_ts = now
            ok2, jb = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                threading.Thread(target=_scene_worker, args=(cam_id, jb.tobytes()),
                                 daemon=True).start()
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
        # a person overlapping a MOVING bicycle is its RIDER -> one cyclist,
        # not a pedestrian + a bike (kills double counting). A parked bike
        # shows no foreground, so it can never suppress real pedestrians
        # walking past it.
        _moving_bikes = [b for b in bikes
                         if fg_ratio_at(fgmask, *b.anchor) >= FG_MIN]
        def _is_rider(d):
            return any(_iou(d.xyxy, b.xyxy) > 0.18 for b in _moving_bikes)
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
            speeds._pedbook = SpeedBook(m_per_px)
            speeds._bikebook = SpeedBook(m_per_px)
        ped_kmh = speeds._pedbook.update(ped_tracks, cts)
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

        ped_in, veh_in_moving, hit_zone = [], [], None
        for z in zones:
            if zone_cooldown.get(z["id"], 0) > cts:
                continue   # fresh episode just ended here — mute duplicates
            zb = z["bbox"]
            pz = [p for tid, p in ped_tracks.items()
                  if z["zone"].contains(p) and not on_island(p)
                  and ped_cb.confirmed(tid) and pedbook.is_moving(tid)]
            # cyclists are crossing users too — a moving car must yield to them
            pz += [p for tid, p in bike_tracks.items()
                   if z["zone"].contains(p) and not on_island(p)
                   and bike_cb.confirmed(tid) and bikebook.is_moving(tid)]
            vz = [tid for tid, p in veh_tracks.items()
                  if zb[0] <= p[0] <= zb[2] and zb[1] <= p[1] <= zb[3]
                  and veh_cb.confirmed(tid) and speeds.is_moving(tid)]
            if pz and vz and len(pz) + len(vz) > len(ped_in) + len(veh_in_moving):
                ped_in, veh_in_moving, hit_zone = pz, vz, z["id"]
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

        def _tid_near(d, tracks):
            ax, ay = d.anchor
            best, bd = None, 45.0
            for tid, (tx, ty) in tracks.items():
                dd = ((ax - tx) ** 2 + (ay - ty) ** 2) ** 0.5
                if dd < bd:
                    best, bd = tid, dd
            return best

        # annotate
        ann = frame.copy()
        for z in zones:
            col = (60, 200, 255) if z["id"] == "main" else (50, 160, 235)
            cv2.polylines(ann, [np.array(z["poly"], np.int32)], True, col, 2)
        for d, tracks, book, col in (
                [(d, ped_tracks, ped_kmh, (90, 230, 120)) for d in active_peds] +
                [(d, veh_tracks, veh_kmh, (80, 150, 255)) for d in active_vehs] +
                [(d, bike_tracks, bike_kmh, (60, 220, 235)) for d in active_bikes]):
            x1, y1, x2, y2 = [int(v) for v in d.xyxy]
            cv2.rectangle(ann, (x1, y1), (x2, y2), col, 2)
            tid = _tid_near(d, tracks)
            v = book.get(tid) if tid is not None else None
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
            if v >= SPEED_LIMIT_KMH * SPEED_FLAG_FACTOR and veh_cb.confirmed(tid):
                speed_streak[tid] = speed_streak.get(tid, 0) + 1
                if speed_streak[tid] >= 2 and tid not in speed_flagged \
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
                                       tracks_json=ep.paths_json())
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
            ok3, buf = cv2.imencode(".jpg", ann, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok3:
                S.jpeg = buf.tobytes()
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
            d["events"] = db.list_events("all", 9, cam_id=d["cam_id"] or None)
            return self._json(200, d)
        if p == "/events.json":
            import urllib.parse
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
        if p.startswith("/snap/"):
            return self._file(os.path.join(SNAP_DIR, os.path.basename(p)), "image/jpeg")
        if p.startswith("/clip/"):
            return self._file(os.path.join(CLIP_DIR, os.path.basename(p)), "video/mp4")
        if p == "/scene.json":
            cam = S.cam_id or cams_load().get("active", "")
            sc = load_scene(cam)
            return self._json(200 if sc else 404, sc or {"ok": False})
        if p == "/frame.jpg":           # single still frame (zone editor canvas)
            with S.lock:
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
            cam = S.cam_id or cams_load().get("active", "")
            json.dump(sc, open(scene_path(cam), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            return self._json(200, {"ok": True, "cam": cam})
        if p == "/admin/scene/recalibrate":
            # wipe the scene -> the worker re-runs the AI calibration
            # (grid + self-check) on the next frames
            if not _admin_ok(self):
                return self._json(403, {"ok": False})
            cam = S.cam_id or cams_load().get("active", "")
            try:
                os.remove(scene_path(cam))
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
