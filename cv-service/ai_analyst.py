# -*- coding: utf-8 -*-
"""Cheap multi-model AI layer.

Roles (cooperating system, each does what it is best at):
- YOLOX (local, free): perception — where are pedestrians/vehicles, every frame.
- Gemini Flash-Lite (pennies): understanding —
  (a) SCENE CONTEXT, once per camera: crossings, signals, flows, scale, pitfalls;
  (b) EVENT ANALYSIS, once per flagged episode: verdict + explanation in PL/EN.
- Humans (free): final verification of every AI verdict.

Cost controls: daily call cap (AI_DAILY_CAP), circuit breaker on repeated
failures, small frames, JSON-only outputs. If AI is down/over cap the site
keeps working — events simply wait as 'ai_skipped' for human review.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

import db

# Backend: "gemini" (cloud, free tier ~1000 req/day) or "local" (Ollama VLM on
# our own server — no cloud, no per-request cost, better privacy).
BACKEND = os.environ.get("AI_BACKEND", "gemini").lower()
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "150"))
# spread the daily budget over the day so a busy morning cannot burn it all
# in two hours and leave the system blind till midnight
HOURLY_CAP = int(os.environ.get("AI_HOURLY_CAP", str(max(4, DAILY_CAP // 20))))
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# local (Ollama) — recommended: qwen2.5vl:3b (Apache-2.0) on a 4c/8GB box,
# fallback moondream. Runs in a background thread, so it never blocks the live
# frame loop; still kept lean (few, small frames) so a verdict lands in ~tens of s.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://patrol-ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b")
LOCAL_MAX_IMAGES = int(os.environ.get("LOCAL_MAX_IMAGES", "4"))
LOCAL_IMG_WIDTH = int(os.environ.get("LOCAL_IMG_WIDTH", "512"))
LOCAL_MAX_TOKENS = int(os.environ.get("LOCAL_MAX_TOKENS", "900"))
LOCAL_TIMEOUT = int(os.environ.get("LOCAL_TIMEOUT", "300"))
# Hard-bound the VLM to a subset of cores so it can never starve the live CV
# worker on a small shared box. The live frame loop keeps the remaining cores.
LOCAL_NUM_THREAD = int(os.environ.get("LOCAL_NUM_THREAD", "2"))


def _shrink_b64_jpeg(b64, width):
    """Downscale a base64 JPEG to `width` px to speed up CPU VLM inference.
    Best-effort: on any failure the original is returned unchanged."""
    try:
        import cv2
        import numpy as np
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return b64
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, max(1, int(h * width / w))),
                             interpolation=cv2.INTER_AREA)
        ok, jb = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return base64.b64encode(jb.tobytes()).decode() if ok else b64
    except Exception:
        return b64

# Circuit breaker applies to the LOCAL model only: if the self-hosted VLM is
# slow/broken we stop hammering it and lean on the free-tier fallback.
_breaker = {"fails": 0, "until": 0.0}

# Free-tier quota guard. A 429 can mean the PER-MINUTE limit (transient) or
# the DAILY limit. Strategy: first 429s back off briefly; if they keep coming
# (3+ within 10 min) it is the daily quota — pause until reset (08:10 UTC,
# DST-proof) and resume automatically. Never retries into paid territory.
_quota = {"until": 0.0, "hits": deque(maxlen=8)}
_hour_budget = {"hour": "", "calls": 0}


class QuotaExhausted(Exception):
    pass


def _quota_reset_ts():
    """Next 08:10 UTC. Gemini free-tier daily quota resets at midnight
    US-Pacific = 07:00 UTC in summer (PDT) / 08:00 UTC in winter (PST).
    Using 08:10 year-round is DST-proof: never resumes BEFORE the real reset
    (an early resume would 429 again and mistakenly pause a whole extra day),
    at worst resumes ~70 min late in summer."""
    now = datetime.now(timezone.utc)
    reset = now.replace(hour=8, minute=10, second=0, microsecond=0)
    if now >= reset:
        reset += timedelta(days=1)
    return reset.timestamp()


# AI_FALLBACK=local: when Gemini is unavailable (daily/hourly quota, outage)
# verdicts are produced by the local Ollama VLM instead; when the quota
# resets, calls flow back to Gemini automatically. On a GPU node the local
# model is fast, so events never wait for midnight.
FALLBACK_LOCAL = os.environ.get("AI_FALLBACK", "").lower() == "local"


def _local_ok():
    return (BACKEND == "local" or FALLBACK_LOCAL) and time.time() >= _breaker["until"]


def _hour_ok():
    h = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if _hour_budget["hour"] != h:
        _hour_budget["hour"] = h
        _hour_budget["calls"] = 0
    return _hour_budget["calls"] < HOURLY_CAP


def _gemini_ok():
    return (bool(API_KEY) and time.time() >= _quota["until"]
            and db.ai_calls_today() < DAILY_CAP and _hour_ok())


def enabled():
    # AI is "on" if either the local VLM or the free-tier fallback can run.
    return _local_ok() or _gemini_ok()


def _trip_breaker():
    _breaker["fails"] += 1
    if _breaker["fails"] >= 3:
        _breaker["until"] = time.time() + 1800  # 30 min off
        _breaker["fails"] = 0


def _call(parts, max_tokens=2000, temperature=0.2, timeout=90):
    """Cooperating multi-model chain. AI_BACKEND=local -> local first, Gemini
    as backup. AI_BACKEND=gemini (+AI_FALLBACK=local) -> Gemini first while
    the free quota lasts, local VLM takes over when it runs out, Gemini
    resumes automatically after the reset."""
    # 1) local-first only when local IS the primary backend
    if BACKEND == "local" and _local_ok():
        try:
            out = _call_local(parts, max_tokens, temperature, timeout=LOCAL_TIMEOUT)
            _breaker["fails"] = 0
            return out
        except Exception as e:
            _trip_breaker()
            print(f"ai_analyst local failed, trying fallback: {e}", flush=True)
    # 2) cloud fallback / primary (Gemini free tier, capped)
    if _gemini_ok():
        try:
            out = _call_gemini(parts, max_tokens, temperature, timeout)
            _hour_budget["calls"] += 1
            return out
        except QuotaExhausted:
            now = time.time()
            _quota["hits"].append(now)
            recent = [t for t in _quota["hits"] if now - t < 600]
            if len(recent) >= 3:
                # repeated 429s -> daily quota really exhausted
                _quota["until"] = _quota_reset_ts()
                print(f"ai_analyst: daily free quota exhausted — paused until "
                      f"{datetime.fromtimestamp(_quota['until'], timezone.utc):%Y-%m-%d %H:%M}"
                      f" UTC, resumes automatically", flush=True)
            else:
                # single 429 may be the per-MINUTE limit -> short backoff
                _quota["until"] = now + 90
                print("ai_analyst: 429 (rate limit) — backing off 90 s", flush=True)
        except Exception as e:
            print(f"ai_analyst gemini error: {e}", flush=True)
    # 3) Gemini exhausted/unavailable -> local VLM keeps verdicts flowing
    #    (fast on a GPU node); Gemini resumes automatically after quota reset
    if FALLBACK_LOCAL and BACKEND != "local" and time.time() >= _breaker["until"]:
        try:
            out = _call_local(parts, max_tokens, temperature, timeout=LOCAL_TIMEOUT)
            _breaker["fails"] = 0
            print("ai_analyst: verdict from LOCAL model (Gemini quota paused)",
                  flush=True)
            return out
        except Exception as e:
            _trip_breaker()
            print(f"ai_analyst local fallback failed: {e}", flush=True)
    return None


def _call_gemini(parts, max_tokens, temperature, timeout):
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "x-goog-api-key": API_KEY})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        if e.code == 429:  # free-tier quota hit — stop until reset, never retry into paid
            raise QuotaExhausted() from e
        raise
    db.ai_call_inc()
    return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])


def _call_local(parts, max_tokens, temperature, timeout):
    """Ollama VLM (e.g. qwen2.5vl:3b / moondream). Images + one text prompt.
    On CPU each image costs a lot, so keep only the most informative frames and
    shrink them; cap generation length. Even so this runs off the frame loop."""
    images = [p["inline_data"]["data"] for p in parts if "inline_data" in p]
    if len(images) > LOCAL_MAX_IMAGES:
        # keep first, last, and evenly-spaced middle frames (the motion story)
        idx = sorted({int(round(i)) for i in
                      [j * (len(images) - 1) / (LOCAL_MAX_IMAGES - 1)
                       for j in range(LOCAL_MAX_IMAGES)]})
        images = [images[i] for i in idx]
    images = [_shrink_b64_jpeg(im, LOCAL_IMG_WIDTH) for im in images]
    prompt = "\n".join(p["text"] for p in parts if "text" in p)
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "images": images,
            "stream": False, "format": "json",
            "options": {"temperature": temperature,
                        "num_predict": min(max_tokens, LOCAL_MAX_TOKENS),
                        "num_thread": LOCAL_NUM_THREAD}}
    req = urllib.request.Request(OLLAMA_URL.rstrip("/") + "/api/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    db.ai_call_inc()
    return json.loads(r["response"])


def _img_part(jpeg_bytes):
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg_bytes).decode()}}


SCENE_PROMPT = """You are a traffic-engineering scene analyst. This is a frame from a fixed public
camera over a road scene in Poland. A COORDINATE GRID is drawn on the image: thin lines every 0.1
of width/height with labels — USE IT to give precise coordinates. Produce a JSON scene context for
a computer-vision pipeline (YOLO detector + zone topology) that must correctly interpret events at
the PEDESTRIAN CROSSINGS. All coordinates are normalized fractions of width/height in [0,1].

Return ONLY JSON with keys:
- description: 2-3 sentences;
- crossings: [{id, polygon:[[x,y]*4..6], approach_directions, signalized:bool}] — one entry PER
  CARRIAGEWAY HALF if a refuge island splits the crossing (e.g. cx1_left, cx1_right), polygon
  covering the FULL zebra stripes area of that half, tight to the painted stripes;
- bike_crossings: [{id, polygon:[[x,y]*4..6]}] — the red/marked CYCLIST crossings (przejazd dla
  rowerzystów) running parallel to zebras; empty array if none;
- islands: [{id, polygon:[[x,y]*3..6]}] — refuge islands / medians where pedestrians WAIT between
  carriageways; a person standing here is NOT on any crossing;
- traffic_lights: [{id, bbox:[x1,y1,x2,y2], controls, visible_at_night:bool}];
- vehicle_flows: [{from, to, lanes, passes_crossing_ids, direction}] — which crossing halves each
  flow crosses and from which side;
- scale_hints: [{feature, approx_pixels_at_full_width:number, approx_meters:number}];
- event_rules: PRECISE per-crossing rules for a REAL "driver failed to yield" HERE: which flow
  vs which crossing half, how the island changes it (vehicle on a half the pedestrian ALREADY
  LEFT = no violation), how signals change it;
- pitfalls: where a naive zone-overlap detector would false-alarm here;
- ignore_regions: array of {label, bbox:[x1,y1,x2,y2]} for every FIXED object a COCO detector is
  likely to MISCLASSIFY as car/person (traffic-light heads, poles, signs, bollards, bins). Tight
  bboxes, be thorough."""

REFINE_PROMPT = """The SAME camera frame now has your proposed geometry DRAWN on it:
crossing polygons in YELLOW with their ids, bike crossings in CYAN, islands in MAGENTA.
Compare the drawn shapes with the actual painted zebras / red bike crossings / islands in the
image (the 0.1 coordinate grid is also drawn). Fix every polygon that is misplaced, too small,
or missing — the polygons must tightly cover the real painted areas. Return the FULL corrected
JSON in exactly the same schema as before (all keys, not only the fixed ones)."""


def scene_context(jpeg_bytes):
    if not enabled():
        return None
    return _call([_img_part(jpeg_bytes), {"text": SCENE_PROMPT}], max_tokens=5000)


def scene_refine(annotated_jpeg_bytes, scene_json):
    """One self-check round: show the model its own polygons drawn on the
    frame and let it correct them. Costs one extra call per camera, hugely
    improves zone placement."""
    if not enabled():
        return None
    parts = [_img_part(annotated_jpeg_bytes),
             {"text": "Previous JSON:\n" + json.dumps(scene_json, ensure_ascii=False)[:6000]
                      + "\n\n" + REFINE_PROMPT}]
    return _call(parts, max_tokens=5000)


EVENT_PROMPT = """You are a road-safety incident analyst for Poland. Polish law (since 1 June 2021):
a driver approaching a pedestrian crossing must slow down and YIELD to a pedestrian ON the crossing
and one ENTERING it. A pedestrian crossing on red has no priority. A stationary vehicle waiting at
a red light near the zebra is NOT a violation.

CRITICAL island rule: a pedestrian standing on a REFUGE ISLAND between carriageways is NOT on the
crossing. A vehicle passing over the half of the crossing the pedestrian has ALREADY LEFT is NOT a
violation. Only a vehicle moving through the half the pedestrian is ON or clearly ENTERING violates.
Apply the per-crossing event_rules from the scene context — they encode which traffic flow conflicts
with which crossing half.

You get {n} chronological frames (~{fps} fps apart) of ONE flagged episode from a fixed camera, plus
scene context and object trajectories. Judge by motion and positions, not identity.

SCENE CONTEXT (JSON): {scene}
TRACK TRAJECTORIES (normalized [0,1] coords, chronological, p=pedestrian v=vehicle b=bike):
{traj}
EPISODE METADATA: traffic-light state seen by pixel analysis: {tl}; max vehicle speed estimate:
{kmh} km/h (rough, monocular); pedestrians in zone: {n_ped}; vehicles in zone: {n_veh}.

Note: cyclists may also use this crossing; a moving motor vehicle must yield to them on
a crossing-with-bike-path the same way. Judge cyclists as crossing users, not violators,
unless they clearly entered on red.

Decide what actually happened. Return ONLY JSON:
- verdict: "violation" | "no_violation" | "uncertain";
- violator: "driver" | "pedestrian" | "none";
- explanation_pl: 2-4 zdania po polsku — co dokładnie widać na klatkach, kto i dlaczego naruszył
  (lub czemu to fałszywy alarm detektora);
- explanation_en: same in English;
- confidence: 0..1 (be honest; low frame rate and blur limit certainty);
- phone_suspect: true|false — EXPERIMENTAL: does any pedestrian's posture (bent head,
  hand at ear/face) suggest phone use while crossing? At this resolution this is a weak
  guess — return false unless clearly visible;
- vulnerable: {{"child": true|false, "stroller": true|false, "wheelchair": true|false}} —
  are clearly visible children, prams/strollers or wheelchair users involved? Mention it
  in the explanations if true (these cases matter most for road-safety statistics);
- what_would_help: one short sentence — what extra data would make this decidable."""


SPEEDING_PROMPT = """You are a road-safety analyst. A monocular CV pipeline flagged a vehicle at
~{kmh} km/h (rough estimate, ±30%, urban limit {limit} km/h) in these {n} chronological frames
(~{fps} fps). You CANNOT measure speed from frames — instead sanity-check the flag:
- is there really ONE vehicle moving visibly faster than surrounding traffic / covering a large
  distance across frames? Or is this a tracker artifact (box jumping between two cars, a bus,
  reflections)?
Return ONLY JSON:
- verdict: "violation" (clearly fast-moving single vehicle, flag plausible) | "no_violation"
  (tracking artifact / normal speed) | "uncertain";
- violator: "driver" | "none";
- explanation_pl: 2-3 zdania po polsku (podkreśl, że pomiar jest szacunkowy);
- explanation_en: same in English;
- confidence: 0..1."""


def analyze_event(frames_jpeg, scene_json, tl_state, kmh, n_ped, n_veh, fps=2,
                  kind="potential_conflict", trajectories=None):
    if not enabled():
        return None
    if kind == "speeding":
        prompt = SPEEDING_PROMPT.format(kmh=kmh if kmh else "?", fps=fps,
                                        n=len(frames_jpeg),
                                        limit=os.environ.get("SPEED_LIMIT_KMH", "50"))
    else:
        prompt = EVENT_PROMPT.format(
            n=len(frames_jpeg), fps=fps,
            scene=json.dumps(scene_json, ensure_ascii=False)[:4000] if scene_json else "unavailable",
            traj=(trajectories or "unavailable")[:2500],
            tl=tl_state or "unknown", kmh=kmh if kmh else "unknown",
            n_ped=n_ped, n_veh=n_veh)
    parts = [_img_part(f) for f in frames_jpeg] + [{"text": prompt}]
    out = _call(parts, max_tokens=1500)
    if not out or "verdict" not in out:
        return None
    if out["verdict"] not in ("violation", "no_violation", "uncertain"):
        out["verdict"] = "uncertain"
    return out
