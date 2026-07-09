"""Ingest: MJPEG-over-HTTP sources + failover pool bound to ONE crossing.

Failover semantics (spec-cameras §4):
- A pool watches ONE and the same crossing; sources are a priority list of
  views of that crossing. Different crossings = separate pools.
- Health: no frame for stall_timeout -> mark down -> switch to next live
  source; hold-down before re-probing a downed source (anti-flapping);
  auto-return to the higher-priority source once it is healthy again.

Frames are yielded in RAM and never persisted.
"""
from __future__ import annotations

import time
import urllib.request

import numpy as np

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


class MjpegHttpSource:
    """Reads an MJPEG (multipart/x-mixed-replace) HTTP stream."""

    def __init__(self, source_id: str, url: str, read_timeout: float = 3.0):
        self.source_id = source_id
        self.url = url
        self.read_timeout = read_timeout
        self._resp = None
        self._buf = b""

    def open(self) -> None:
        self._resp = urllib.request.urlopen(self.url, timeout=self.read_timeout)
        self._buf = b""

    def read(self) -> np.ndarray | None:
        """Return next decoded frame or raise on stream failure."""
        import cv2
        if self._resp is None:
            self.open()
        while True:
            start = self._buf.find(SOI)
            if start != -1:
                end = self._buf.find(EOI, start + 2)
                if end != -1:
                    jpg = self._buf[start:end + 2]
                    self._buf = self._buf[end + 2:]
                    frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        return frame
                    continue
            chunk = self._resp.read(16384)
            if not chunk:
                raise ConnectionError(f"{self.source_id}: stream ended")
            self._buf += chunk

    def close(self) -> None:
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass
        self._resp = None
        self._buf = b""


class FailoverPool:
    """Priority failover across sources of ONE crossing."""

    def __init__(self, crossing_id: str, sources: list[MjpegHttpSource],
                 stall_timeout: float = 4.0, holddown: float = 8.0,
                 health_cb=None):
        if not sources:
            raise ValueError("empty source pool")
        self.crossing_id = crossing_id
        self.sources = sources
        self.stall_timeout = stall_timeout
        self.holddown = holddown
        self.health_cb = health_cb or (lambda *a, **k: None)
        self.active_idx: int | None = None
        self._down_until: dict[int, float] = {}
        self._had_active = False  # a prior source existed => next pick is a failover

    def _mark_down(self, idx: int, reason: str) -> None:
        now = time.time()
        self._down_until[idx] = now + self.holddown
        self.sources[idx].close()
        self.health_cb(now, self.sources[idx].source_id, "down", reason)

    def _try_open(self, idx: int) -> bool:
        src = self.sources[idx]
        try:
            src.close()
            src.open()
            deadline = time.time() + self.stall_timeout
            while time.time() < deadline:
                if src.read() is not None:
                    return True
            return False
        except Exception:
            return False

    def _pick(self) -> int | None:
        now = time.time()
        for idx in range(len(self.sources)):
            if now < self._down_until.get(idx, 0):
                continue  # hold-down: not probed again yet (anti-flapping)
            if self._try_open(idx):
                if self.active_idx != idx:
                    self.health_cb(now, self.sources[idx].source_id,
                                   "failover" if self._had_active else "up",
                                   f"active={self.sources[idx].source_id}")
                self._had_active = True
                return idx
            self._mark_down(idx, "probe failed")
        return None

    def frames(self, duration: float | None = None):
        """Yield (ts, frame, source_id); handles failover transparently."""
        t_end = None if duration is None else time.time() + duration
        while t_end is None or time.time() < t_end:
            if self.active_idx is None:
                self.active_idx = self._pick()
                if self.active_idx is None:
                    time.sleep(0.5)
                    continue
            src = self.sources[self.active_idx]
            try:
                frame = src.read()
            except Exception as e:
                self._mark_down(self.active_idx, f"read error: {e}")
                self.active_idx = None
                continue
            now = time.time()
            # auto-return: if a higher-priority source has cleared hold-down, probe it
            for idx in range(self.active_idx):
                if now >= self._down_until.get(idx, 0) and self._try_open(idx):
                    self.health_cb(now, self.sources[idx].source_id, "recovered",
                                   f"failback from {src.source_id}")
                    self.sources[self.active_idx].close()
                    self.active_idx = idx
                    src = self.sources[idx]
                    try:
                        frame = src.read()
                    except Exception as e:
                        self._mark_down(idx, f"failback read error: {e}")
                        self.active_idx = None
                        frame = None
                    break
                elif now >= self._down_until.get(idx, 0):
                    self._down_until[idx] = now + self.holddown  # still dead, re-arm
            if frame is not None:
                yield now, frame, src.source_id
