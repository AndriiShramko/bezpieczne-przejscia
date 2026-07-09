"""Ephemeral tracking — IDs live in RAM only and are never persisted.

Default: deterministic nearest-centroid tracker (dependency-free).
Production option: supervision ByteTrack (MIT) via ByteTrackAdapter.
"""
from __future__ import annotations

import itertools

from .detect import Detection


class CentroidTracker:
    def __init__(self, max_dist: float = 80.0, ttl: int = 10):
        self._ids = itertools.count(1)
        self._tracks: dict[int, tuple[float, float]] = {}
        self._misses: dict[int, int] = {}
        self.max_dist = max_dist
        self.ttl = ttl

    def update(self, detections: list[Detection]) -> dict[int, tuple[float, float]]:
        anchors = [d.anchor for d in detections]
        assigned: dict[int, tuple[float, float]] = {}
        free = set(range(len(anchors)))
        # greedy nearest match, deterministic order
        for tid in sorted(self._tracks):
            tx, ty = self._tracks[tid]
            best, best_d = None, self.max_dist
            for i in sorted(free):
                ax, ay = anchors[i]
                d = ((ax - tx) ** 2 + (ay - ty) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                free.discard(best)
                assigned[tid] = anchors[best]
                self._misses[tid] = 0
        for i in sorted(free):
            tid = next(self._ids)
            assigned[tid] = anchors[i]
            self._misses[tid] = 0
        # age out lost tracks (ephemeral — state simply vanishes)
        for tid in list(self._tracks):
            if tid not in assigned:
                self._misses[tid] = self._misses.get(tid, 0) + 1
                if self._misses[tid] <= self.ttl:
                    assigned[tid] = self._tracks[tid]
                else:
                    self._misses.pop(tid, None)
        self._tracks = {t: p for t, p in assigned.items()}
        return dict(self._tracks)


class ByteTrackAdapter:
    """supervision.ByteTrack (MIT) behind the same update() contract."""

    def __init__(self, frame_rate: int = 10):
        import numpy as np
        import supervision as sv
        self._sv = sv
        self._np = np
        self._bt = sv.ByteTrack(frame_rate=frame_rate)

    def update(self, detections: list[Detection]) -> dict[int, tuple[float, float]]:
        np = self._np
        sv = self._sv
        if not detections:
            dets = sv.Detections.empty()
        else:
            dets = sv.Detections(
                xyxy=np.array([d.xyxy for d in detections], dtype=np.float32),
                confidence=np.array([d.conf for d in detections], dtype=np.float32),
                class_id=np.zeros(len(detections), dtype=int),
            )
        tracked = self._bt.update_with_detections(dets)
        out: dict[int, tuple[float, float]] = {}
        for xyxy, tid in zip(tracked.xyxy, tracked.tracker_id):
            if tid is not None:
                x1, y1, x2, y2 = xyxy
                out[int(tid)] = (float((x1 + x2) / 2), float(y2))
        return out
