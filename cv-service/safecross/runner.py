"""Main loop: frames -> detect -> blur -> track -> zones -> aggregate buckets.

Privacy order of operations (spec-legal §3/§3a):
- The raw frame exists only transiently in RAM during inference.
- Privacy regions (heads, plate strips) are pixelated immediately after
  detection; the raw reference is dropped in the same iteration.
- Nothing image-like is ever written to disk; only counters reach storage.
"""
from __future__ import annotations

from .blur import blur_regions, head_region
from .detect import PERSON, VEHICLE
from .storage import Store
from .zones import CrossingMetrics

MAX_FRAME_GAP_SEC = 2.0  # coverage accrual cap between frames


def plate_region(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Bottom-center strip of a vehicle box ≈ plate area."""
    x1, y1, x2, y2 = box
    w = x2 - x1
    return int(x1 + 0.25 * w), int(y2 - 0.22 * (y2 - y1)), int(x2 - 0.25 * w), int(y2)


def run(pool, detector, ped_tracker, veh_tracker, metrics: CrossingMetrics,
        store: Store, duration: float | None = None, on_frame=None) -> int:
    """Process the pool; returns number of frames handled."""
    crossing_id = pool.crossing_id
    n = 0
    last_ts: float | None = None
    for ts, frame, source_id in pool.frames(duration=duration):
        detections = detector.detect(frame)

        # privacy first: pixelate heads + plate strips, drop raw reference
        privacy_boxes = [head_region(d.xyxy) for d in detections if d.cls == PERSON]
        privacy_boxes += [plate_region(d.xyxy) for d in detections if d.cls == VEHICLE]
        frame = blur_regions(frame, privacy_boxes)  # raw frame ref replaced

        ped_tracks = ped_tracker.update([d for d in detections if d.cls == PERSON])
        veh_tracks = veh_tracker.update([d for d in detections if d.cls == VEHICLE])

        counters = metrics.update(ts, ped_tracks, veh_tracks)
        for metric, value in counters.items():
            if value:
                store.add_stat(crossing_id, ts, metric, value)

        # anti-fabrication: a gap longer than MAX_FRAME_GAP_SEC (e.g. a
        # failover switch) contributes ZERO observed time, not a capped slice
        gap = 0.0 if last_ts is None else max(0.0, ts - last_ts)
        observed = gap if 0.0 < gap <= MAX_FRAME_GAP_SEC else 0.0
        if observed:
            store.add_coverage(crossing_id, ts, observed, source_id)
        last_ts = ts

        n += 1
        if on_frame is not None:
            on_frame(ts, frame, source_id, counters)
    return n
