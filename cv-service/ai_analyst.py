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

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "150"))
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

_breaker = {"fails": 0, "until": 0.0}


def enabled():
    return bool(API_KEY) and time.time() >= _breaker["until"] and db.ai_calls_today() < DAILY_CAP


def _call(parts, max_tokens=2000, temperature=0.2, timeout=90):
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "x-goog-api-key": API_KEY})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        db.ai_call_inc()
        txt = r["candidates"][0]["content"]["parts"][0]["text"]
        _breaker["fails"] = 0
        return json.loads(txt)
    except Exception as e:
        _breaker["fails"] += 1
        if _breaker["fails"] >= 3:
            _breaker["until"] = time.time() + 1800  # 30 min off
            _breaker["fails"] = 0
        print(f"ai_analyst error: {e}", flush=True)
        return None


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
- pitfalls: where a naive zone-overlap detector would false-alarm here (waiting cars, islands...)."""


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
