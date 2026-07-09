"""Deterministic SYNTHETIC crossing scene (no real footage anywhere).

Convention consumed by safecross.detect.BlobDetector:
- pedestrians = pure-green rectangles, vehicles = pure-red rectangles,
  near-black background, faint gray zebra stripes (below blob threshold).

The scene loops every PERIOD seconds:
- one pedestrian crosses the road vertically (crosses the ped line once),
- one vehicle drives through horizontally WHILE the pedestrian is on the
  crosswalk (yield violation by topology) and crosses the veh line once.
"""
from __future__ import annotations

import numpy as np

W, H = 640, 360
PERIOD = 10.0

# geometry shared with tests
PED_LINE = ((200.0, 180.0), (440.0, 180.0))   # horizontal, mid-road
VEH_LINE = ((320.0, 120.0), (320.0, 240.0))   # vertical, mid-crosswalk
CROSSWALK = [(200.0, 140.0), (440.0, 140.0), (440.0, 220.0), (200.0, 220.0)]


def render(t: float) -> np.ndarray:
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    # faint zebra stripes (kept below detector thresholds)
    for x in range(210, 440, 40):
        frame[150:210, x:x + 18] = 60

    phase = t % PERIOD

    # pedestrian: x=310, y from -30 to 390 during phase [1, 7]
    # (overlaps the vehicle transit -> one yield violation per loop)
    if 1.0 <= phase <= 7.0:
        y = -30 + ((phase - 1.0) / 6.0) * 420
        y0, y1 = int(y), int(y) + 28
        x0, x1 = 303, 317
        ys, ye = max(0, y0), min(H, y1)
        if ye > ys:
            frame[ys:ye, x0:x1] = (0, 255, 0)  # pure green = person

    # vehicle: y=176, x from -60 to 700 during phase [2, 7]
    if 2.0 <= phase <= 7.0:
        x = -60 + ((phase - 2.0) / 5.0) * 760
        x0, x1 = int(x), int(x) + 46
        xs, xe = max(0, x0), min(W, x1)
        if xe > xs:
            frame[176:200, xs:xe] = (0, 0, 255)  # pure red = vehicle

    return frame
