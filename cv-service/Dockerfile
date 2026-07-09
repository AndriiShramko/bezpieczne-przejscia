FROM python:3.12-slim

# opencv-headless runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# deno (JS runtime required by yt-dlp for YouTube extraction)
RUN curl -fsSL -o /tmp/deno.zip \
      https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip \
    && chmod +x /usr/local/bin/deno

RUN pip install --no-cache-dir \
      numpy opencv-python-headless onnxruntime yt-dlp

WORKDIR /app
# YOLOX-s detector weights (Apache-2.0, Megvii). Downloaded at build so the
# repo needs no large binary. sha256 c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063
RUN mkdir -p /app/models && curl -fsSL -o /app/models/yolox_s.onnx \
      https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
COPY safecross /app/safecross
COPY worker.py /app/worker.py

ENV DENO_PATH=/usr/local/bin/deno \
    YTDLP_BIN=yt-dlp \
    MODEL_PATH=/app/models/yolox_s.onnx \
    DB_PATH=/data/events.db \
    SNAP_DIR=/data/snapshots \
    PORT=8090 \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8090
CMD ["python", "/app/worker.py"]
