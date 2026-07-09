"""Irreversible pixelation of privacy regions (faces / plates) at ingest.

Applied to the frame BEFORE any downstream processing or preview.
This module never persists anything; frames exist only in RAM.
"""
from __future__ import annotations

import numpy as np


def blur_regions(frame: np.ndarray, boxes: list[tuple[int, int, int, int]],
                 block: int = 12) -> np.ndarray:
    """Return a copy of frame with each (x1,y1,x2,y2) box pixelated.

    Pixelation (downsample->upsample) destroys the region irreversibly at
    the chosen block size; no original pixels of the region survive.
    """
    out = frame.copy()
    h, w = out.shape[:2]
    for x1, y1, x2, y2 in boxes:
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        roi = out[y1:y2, x1:x2]
        rh, rw = roi.shape[:2]
        small = roi[:: max(1, rh // max(1, rh // block)), :: max(1, rw // max(1, rw // block))]
        # coarse mean-pool then nearest-upscale
        ys = np.linspace(0, rh, num=max(1, rh // block) + 1, dtype=int)
        xs = np.linspace(0, rw, num=max(1, rw // block) + 1, dtype=int)
        for i in range(len(ys) - 1):
            for j in range(len(xs) - 1):
                cell = roi[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
                if cell.size:
                    cell[:] = cell.mean(axis=(0, 1), keepdims=True).astype(cell.dtype)
        out[y1:y2, x1:x2] = roi
        del small
    return out


def head_region(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Top ~28% of a person box ≈ head region to pixelate (no landmarks,
    no embeddings — geometry only)."""
    x1, y1, x2, y2 = box
    return int(x1), int(y1), int(x2), int(y1 + 0.28 * (y2 - y1))
