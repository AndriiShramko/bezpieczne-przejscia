"""Detector interface + implementations.

License policy (spec-cv §2): served/networked artifacts must be 0-copyleft.
Allowed detector weights/code: YOLOX (Apache-2.0) or original RT-DETR
(Apache-2.0). Ultralytics YOLO (AGPL-3.0) is FORBIDDEN anywhere in this tree.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PERSON = "person"
VEHICLE = "vehicle"
BIKE = "bike"

# COCO ids -> aggregate classes. Bicycles are their own class (crossing users,
# separate stats); motorcycles stay VEHICLE — a motor vehicle must yield, so it
# participates in violation logic like a car.
COCO_MAP = {0: PERSON, 1: BIKE, 2: VEHICLE, 3: VEHICLE, 5: VEHICLE, 7: VEHICLE}


@dataclass
class Detection:
    xyxy: tuple[float, float, float, float]
    cls: str          # person | vehicle
    conf: float

    @property
    def anchor(self) -> tuple[float, float]:
        """Bottom-center of the box — ground-contact proxy."""
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, y2)


class BlobDetector:
    """Deterministic detector for SYNTHETIC test frames only.

    Convention of the synthetic generator: pedestrians are pure-green
    rectangles, vehicles are pure-red rectangles on near-black background.
    Enables full end-to-end runs (ingest->blur->track->zones->buckets)
    without any real footage (legal gate: no real frames before LIA/DPIA).
    """

    def __init__(self, min_area: int = 60):
        self.min_area = min_area

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import cv2
        out: list[Detection] = []
        for channel, cls in ((1, PERSON), (2, VEHICLE)):  # BGR: G=person, R=vehicle
            mask = (frame[:, :, channel] > 180) & (frame.sum(axis=2) < 700)
            m8 = mask.astype(np.uint8) * 255
            contours, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x, y, w, h = cv2.boundingRect(c)
                if w * h >= self.min_area:
                    out.append(Detection((x, y, x + w, y + h), cls, 1.0))
        return out


class OnnxCocoDetector:
    """ONNX Runtime wrapper for an Apache-2.0 COCO detector (YOLOX-s export).

    Weights are NOT bundled; download separately and verify the license of
    both code and weights (Apache-2.0 for Megvii YOLOX). AGPL models are
    rejected by policy and by the SBOM gate.
    """

    def __init__(self, onnx_path: str, input_size: int = 640, conf_thres: float = 0.4):
        import os
        import onnxruntime as ort
        so = ort.SessionOptions()
        # Bound CPU threads so the detector shares a small box with a local VLM
        # without oversubscribing cores (context-switch thrash). ONNX_THREADS=0
        # keeps the ORT default (all cores).
        nthreads = int(os.environ.get("ONNX_THREADS", "0"))
        if nthreads > 0:
            so.intra_op_num_threads = nthreads
            so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(onnx_path, sess_options=so,
                                         providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name
        self.size = input_size
        self.conf = conf_thres

    def _preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        import cv2
        h, w = frame.shape[:2]
        r = min(self.size / h, self.size / w)
        resized = cv2.resize(frame, (int(w * r), int(h * r)))
        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        img = canvas.transpose(2, 0, 1)[None].astype(np.float32)
        return img, r

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import cv2
        img, r = self._preprocess(frame)
        pred = self.sess.run(None, {self.input_name: img})[0][0]  # (N, 85) YOLOX raw grid
        pred = self._decode(pred)
        boxes, scores, classes = [], [], []
        for row in pred:
            obj = row[4]
            cls_id = int(np.argmax(row[5:]))
            score = float(obj * row[5 + cls_id])
            if score < self.conf or cls_id not in COCO_MAP:
                continue
            cx, cy, w, h = row[:4]
            boxes.append([cx - w / 2, cy - h / 2, w, h])
            scores.append(score)
            classes.append(cls_id)
        out: list[Detection] = []
        if boxes:
            keep = cv2.dnn.NMSBoxes(boxes, scores, self.conf, 0.45)
            for i in np.array(keep).flatten():
                x, y, w, h = boxes[i]
                out.append(Detection(
                    (x / r, y / r, (x + w) / r, (y + h) / r),
                    COCO_MAP[classes[i]], scores[i]))
        return out

    def _decode(self, pred: np.ndarray) -> np.ndarray:
        """YOLOX grid decode (strides 8/16/32)."""
        grids, strides = [], []
        for stride in (8, 16, 32):
            n = self.size // stride
            ys, xs = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), stride))
        g = np.concatenate(grids, 0)
        s = np.concatenate(strides, 0)
        pred = pred.copy()
        pred[:, :2] = (pred[:, :2] + g) * s
        pred[:, 2:4] = np.exp(pred[:, 2:4]) * s
        return pred
