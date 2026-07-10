# Bezpieczne Przejścia / SafeCross — private deploy repo

Privacy-first, **aggregate-only** pedestrian-crossing safety analytics
demonstrator. Site: patrol.flyreelstudio.eu (PL/EN). This is the PRIVATE
repository (deploy configs + runbook). The public mirror
(`bezpieczne-przejscia`) contains the same code with synthetic assets and
no infrastructure details.

**Hard rules (see vault specs `traffic-ai-bydgoszcz/`):**
- No real camera frames before the lawyer-signed LIA/DPIA (legal gate).
- Client-facing showcase only on a permissioned or own camera (GATE 0).
- Disk = counters only (`stats_bucket` + `coverage_bucket` + `camera_health`);
  no frames, no embeddings, ephemeral track IDs in RAM.
- Detector: Apache-2.0 only (YOLOX/RT-DETR). No AGPL anywhere.
- Secrets only in `deploy/config.env` on the server (chmod 600), never in git.

## Layout
- `pipeline/` — safecross package (ingest+failover, detect, blur, track,
  zones, aggregate storage) + tests (incl. the failover fact-test).
- `site/` — static-site generator (PL root + /en/), synthetic demo data.
- `form-proxy/` — lead form -> Telegram proxy (token via env).
- `deploy/` — docker-compose with hard caps + operator runbook.
- `models/` — NOT in git. YOLOX-s ONNX (Apache-2.0):
  https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
  sha256 c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063

## Run tests
```bash
python -m venv .venv && .venv/Scripts/pip install -r pipeline/requirements.txt
cd pipeline && ../.venv/Scripts/python -m pytest tests/ -q
```

## Build site
```bash
cd site && python make_demo_data.py && python build_site.py
```

License: Apache-2.0. Author: Andrii Shramko (zmei116@gmail.com).
