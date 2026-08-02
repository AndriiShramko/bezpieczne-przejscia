# NVIDIA Cosmos 3 — candidate for the local-VLM verdict layer

Research note. Not integrated yet. Logged here so the idea isn't lost between
sessions — see the "Local-VLM fallback, wired and dormant" section of the
root [README.md](../../README.md) and the `AI_BACKEND=local` path in
`cv-service/ai_analyst.py` for the code this would plug into.

## What it is

NVIDIA launched **Cosmos 3** on 2026-06-01: an open, two-tower
mixture-of-transformers world foundation model for "physical AI" — a
reasoning transformer paired with an expert generation transformer, trained
on billions of text/image/video/sound/action samples. It understands object
interactions, motion and spatio-temporal relationships before generating
video/action predictions. SOTA on R-Bench, and leads PAI-Bench, Physics-IQ,
RoboLab.

Two variants matter for this project:

- **Cosmos 3 Edge** — compact **4B model with a 2B reasoner**, built for
  real-time **on-device** video understanding. NVIDIA explicitly calls out
  **traffic monitoring** as a target use case: real-time inference on Jetson
  Thor for vision agents reasoning over live video streams, with road-scene
  understanding, traffic reasoning and object-intent prediction. Reported
  numbers: 0.93–0.97 IoU person localization, 63.5% event-detection recall
  on real **low-light surveillance footage**.
- **Cosmos Reason 2** — 2B/8B reasoning VLM, **256K-token context** (vs 16K
  in Reason 1), so it can look at a much longer clip/richer scene per call
  than an 8-frame sequence.

License: **OpenMDW-1.1** (Linux Foundation) — permissive, commercial use and
derivatives allowed, no AGPL-style copyleft. Compatible with this repo's
"0-AGPL licensing policy" constraint.

## Why it's relevant here

This repo already has a dormant local-VLM path (`AI_BACKEND=local`, Ollama +
`qwen2.5vl:3b`), disabled because a verdict took 2–3 minutes on a 4-vCPU CPU
box — unusable for timely analysis, per README's "Honest limitations".
Cosmos 3 Edge is aimed at exactly that gap:

- **Real-time on a small edge box, not 2–3 min on CPU.** If it holds up off
  Jetson Thor (needs testing — see below), it could turn the local-VLM
  fallback from "wired and dormant" into "wired and usable", replacing or
  complementing the Gemini Flash-Lite free-tier call (with its 429
  pause/resume dance and hard daily cap).
- **Traffic/road-scene reasoning as a first-class target**, not a generic
  VLM asked to describe a photo — closer semantic fit for "walking
  pedestrian × moving vehicle in the same zone" verdicts than a
  general-purpose model.
- **Low-light recall was measured explicitly** — one of this project's weak
  spots (night footage, per `docs/img/live-night-zones.jpg`).
- **Longer context (Cosmos Reason 2, 256K tokens)** could let a verdict see
  more of the episode clip / pre-roll than the current 8-frame sequence,
  possibly reducing hallucinated verdicts (README's "The VLM can
  hallucinate" limitation).
- **Cosmos Predict2 can generate synthetic traffic-anomaly video** (see the
  `its-accident` post-training recipe in the
  [Cosmos Cookbook](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/post_training/predict2/its-accident/post_training.html)).
  Real violation events are rare and hard to collect at volume; synthetic
  anomaly generation is a plausible way to get more training/eval data for
  the detector or for prompt/eval-tuning the verdict model, without waiting
  for enough real incidents to accumulate.

## Open questions before trying this

- Does Cosmos 3 Edge run acceptably on the CPU-only VPS this project targets
  ($0/month constraint), or does it require a GPU/Jetson host the way the
  current local-VLM path effectively does? If GPU-only, it's still worth
  testing per the README's own contribution ask ("local VLM benchmarks on
  GPU hosts").
- Actual latency/accuracy on *this* project's real inputs (blurred,
  low-res, low-fps episode clips) vs. NVIDIA's own benchmark conditions.
- Output format compatibility with the existing `ai_analyst.py` verdict
  schema (PL/EN explanation text), or whether an adapter layer is needed.
- Whether Cosmos Predict2 synthetic clips are realistic enough to matter for
  a YOLOX-s detector this small, or whether they'd only help the VLM side.

## Sources

- [NVIDIA Newsroom — NVIDIA Launches Cosmos 3](https://nvidianews.nvidia.com/news/nvidia-launches-cosmos-3-the-open-frontier-foundation-model-for-physical-ai)
- [NVIDIA Blog — How Cosmos 3 Helps Physical AI Think Before It Acts](https://blogs.nvidia.com/blog/cosmos-3-physical-ai-open-world-foundation-model/)
- [MarkTechPost — Cosmos 3 architecture writeup](https://www.marktechpost.com/2026/06/03/nvidia-releases-cosmos-3-a-two-tower-mixture-of-transformers-foundation-model-unifying-physical-reasoning-world-generation-and-action-generation/)
- [Centific — Cosmos 3 Edge on Jetson Thor for traffic monitoring](https://www.centific.com/blog/centific-brings-real-time-physical-ai-to-the-edge-with-nvidia-cosmos-3-edge)
- [VAST Data — Cosmos Reason smart cities](https://www.vastdata.com/blog/vast-nvidia-cosmos-reason-smart-cities)
- [Cosmos Cookbook — Traffic Anomaly Generation with Cosmos Predict2](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/post_training/predict2/its-accident/post_training.html)
- [GitHub — NVIDIA/cosmos](https://github.com/nvidia/cosmos)
