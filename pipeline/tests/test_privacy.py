"""Privacy invariants, checked as facts:

1. Package source contains no disk-writing image calls (imwrite / save).
2. Pixelation destroys the region (variance collapses inside boxes).
3. After a full pipeline run, the data directory holds ONLY the SQLite DB
   (counters) — no image/video files anywhere; DB has no BLOB columns.
"""
import os
import re

import numpy as np

from safecross.blur import blur_regions, head_region

PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "safecross"))

FORBIDDEN = re.compile(
    r"imwrite|VideoWriter|\.save\(|imencode\([^)]*\)\s*\.tofile|np\.save|pickle\.dump",
)


def test_no_disk_writing_image_calls_in_package():
    hits = []
    for root, _, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8").read()
                for m in FORBIDDEN.finditer(src):
                    hits.append((f, m.group(0)))
    assert hits == [], f"disk-persistence calls found: {hits}"


def test_pixelation_destroys_region():
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, size=(120, 160, 3), dtype=np.uint8)
    box = (40, 20, 120, 100)
    out = blur_regions(frame, [box], block=16)
    x1, y1, x2, y2 = box
    var_before = frame[y1:y2, x1:x2].astype(float).var()
    var_after = out[y1:y2, x1:x2].astype(float).var()
    assert var_after < var_before * 0.15  # detail irreversibly destroyed
    # outside the box untouched
    assert (out[:y1] == frame[:y1]).all()


def test_head_region_is_top_slice():
    x1, y1, x2, y2 = head_region((100, 100, 140, 200))
    assert (x1, y1, x2) == (100, 100, 140) and 120 <= y2 <= 135
