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
import urllib.request

import db

# Backend: "gemini" (cloud, free tier ~1000 req/day) or "local" (Ollama VLM on
# our own server — no cloud, no per-request cost, better privacy).
BACKEND = os.environ.get("AI_BACKEND", "gemini").lower()
API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "150"))
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# local (Ollama) — recommended: qwen2.5vl:3b (Apache-2.0) on a 4c/8GB box,
# fallback moondream. One call per event, ~10-20 s on CPU.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://patrol-ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b")

_breaker = {"fails": 0, "until": 0.0}


def enabled():
    if time.time() < _breaker["until"]:
        return False
    if BACKEND == "local":
        return True
    return bool(API_KEY) and db.ai_calls_today() < DAILY_CAP


def _call(parts, max_tokens=2000, temperature=0.2, timeout=90):
    try:
        if BACKEND == "local":
            out = _call_local(parts, max_tokens, temperature, timeout=max(timeout, 180))
        else:
            out = _call_gemini(parts, max_tokens, temperature, timeout)
        _breaker["fails"] = 0
        return out
    except Exception as e:
        _breaker["fails"] += 1
        if _breaker["fails"] >= 3:
            _breaker["until"] = time.time() + 1800  # 30 min off
            _breaker["fails"] = 0
        print(f"ai_analyst error ({BACKEND}): {e}", flush=True)
        return None


def _call_gemini(parts, max_tokens, temperature, timeout):
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "x-goog-api-key": API_KEY})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    db.ai_call_inc()
    return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])


def _call_local(parts, max_tokens, temperature, timeout):
    """Ollama VLM (e.g. qwen2.5vl:3b / moondream). Images + one text prompt."""
    images = [p["inline_data"]["data"] for p in parts if "inline_data" in p]
    prompt = "\n".join(p["text"] for p in parts if "text" in p)
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "images": images,
            "stream": False, "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens}}
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
camera over a road scene in Poland. Produce a JSON scene context for a computer-vision pipeline
(YOLO detector + zone topology) that must correctly interpret events at the PEDESTRIAN CROSSINGS.
All coordinates are normalized fractions of image width/height in [0,1].

Return ONLY JSON with keys:
- description: 2-3 sentences;
- crossings: [{id, polygon:[[x,y]*4..6], approach_directions, signalized:bool}];
- traffic_lights: [{id, bbox:[x1,y1,x2,y2], controls, visible_at_night:bool}];
- vehicle_flows: [{from, to, lanes, passes_crossing_ids}];
- scale_hints: [{feature, approx_pixels_at_full_width:number, approx_meters:number}] — pick
  measurable features (PL lane ~3.5 m, zebra stripe 0.5 m, crossing length);
- event_rules: short rules for a REAL "driver failed to yield" at THESE crossings, including how
  signals change interpretation;
- pitfalls: where a naive zone-overlap detector would false-alarm here (waiting cars, islands...);
- ignore_regions: CRITICAL — array of {label, bbox:[x1,y1,x2,y2]} for every FIXED object a COCO
  detector (YOLO) is likely to MISCLASSIFY as a car or a person: traffic-light heads and their
  poles, road signs on poles, bollards/posts, statues/monuments, illuminated shop signs, parked-
  forever objects, litter bins. Give a tight bbox for each. The pipeline uses these to suppress
  false "car"/"person" detections on static street furniture, so be thorough."""


def scene_context(jpeg_bytes):
    if not enabled():
        return None
    return _call([_img_part(jpeg_bytes), {"text": SCENE_PROMPT}], max_tokens=4000)


EVENT_PROMPT = """You are a road-safety incident analyst for Poland. Polish law (since 1 June 2021):
a driver approaching a pedestrian crossing must slow down and YIELD to a pedestrian ON the crossing
and one ENTERING it. A pedestrian crossing on red has no priority. A stationary vehicle waiting at
a red light near the zebra is NOT a violation.

You get {n} chronological frames (~{fps} fps apart) of ONE flagged episode from a fixed camera, plus
scene context. Faces/plates are blurred for privacy — judge by motion and positions, not identity.

SCENE CONTEXT (JSON): {scene}
EPISODE METADATA: traffic-light state seen by pixel analysis: {tl}; max vehicle speed estimate:
{kmh} km/h (rough, monocular); pedestrians in zone: {n_ped}; vehicles in zone: {n_veh}.

Decide what actually happened. Return ONLY JSON:
- verdict: "violation" | "no_violation" | "uncertain";
- violator: "driver" | "pedestrian" | "none";
- explanation_pl: 2-4 zdania po polsku — co dokładnie widać na klatkach, kto i dlaczego naruszył
  (lub czemu to fałszywy alarm detektora);
- explanation_en: same in English;
- confidence: 0..1 (be honest; low frame rate and blur limit certainty);
- what_would_help: one short sentence — what extra data would make this decidable."""


def analyze_event(frames_jpeg, scene_json, tl_state, kmh, n_ped, n_veh, fps=2):
    if not enabled():
        return None
    prompt = EVENT_PROMPT.format(
        n=len(frames_jpeg), fps=fps,
        scene=json.dumps(scene_json, ensure_ascii=False)[:4000] if scene_json else "unavailable",
        tl=tl_state or "unknown", kmh=kmh if kmh else "unknown",
        n_ped=n_ped, n_veh=n_veh)
    parts = [_img_part(f) for f in frames_jpeg] + [{"text": prompt}]
    out = _call(parts, max_tokens=1500)
    if not out or "verdict" not in out:
        return None
    if out["verdict"] not in ("violation", "no_violation", "uncertain"):
        out["verdict"] = "uncertain"
    return out
