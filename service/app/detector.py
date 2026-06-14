"""YuNet face detection — identical algorithm to the integration's
in-process version, just run inside the service container where
onnxruntime is installable.

Decoding follows OpenCV's reference cv::FaceDetectorYN:
- score per cell  = sqrt(cls * obj)
- bbox per cell   = (cx, cy) = (col + bbox[0], row + bbox[1]) * stride
                    (w,  h)  = exp(bbox[2..3]) * stride
- strides         = (8, 16, 32)
- NMS             = greedy IoU
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import onnxruntime as ort
from PIL import Image

_LOGGER = logging.getLogger(__name__)

_INPUT_W = 640
_INPUT_H = 640
_STRIDES = (8, 16, 32)


@dataclass(slots=True)
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    confidence: float

    def as_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "w": round(self.w, 4),
            "h": round(self.h, 4),
            "confidence": round(self.confidence, 3),
        }


@dataclass(slots=True)
class DetectionResult:
    faces: list[FaceBox]
    image_width: int
    image_height: int
    detection_ms: int


class FaceDetector:
    def __init__(
        self,
        model_path: str,
        min_confidence: float = 0.6,
        nms_threshold: float = 0.3,
    ) -> None:
        self._min_confidence = float(min_confidence)
        self._nms_threshold = float(nms_threshold)

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        sess_opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]

    def detect(
        self,
        image_bytes: bytes,
        min_confidence: float | None = None,
    ) -> DetectionResult:
        threshold = (
            self._min_confidence if min_confidence is None else float(min_confidence)
        )
        start = time.monotonic()

        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
        except Exception as err:
            _LOGGER.warning("Failed to decode image: %s", err)
            return DetectionResult([], 0, 0, 0)

        orig_w, orig_h = img.size

        scale = min(_INPUT_W / orig_w, _INPUT_H / orig_h)
        new_w = max(1, int(orig_w * scale))
        new_h = max(1, int(orig_h * scale))
        resized = img.resize((new_w, new_h), Image.BILINEAR)
        canvas = Image.new("RGB", (_INPUT_W, _INPUT_H), (114, 114, 114))
        canvas.paste(resized, (0, 0))

        rgb = np.asarray(canvas, dtype=np.float32)
        bgr = rgb[:, :, ::-1]
        chw = np.ascontiguousarray(bgr.transpose(2, 0, 1)[np.newaxis])

        outputs = self._session.run(self._output_names, {self._input_name: chw})
        cls_outs = outputs[0:3]
        obj_outs = outputs[3:6]
        bbox_outs = outputs[6:9]

        all_boxes: list[np.ndarray] = []
        all_scores: list[np.ndarray] = []

        for stride_idx, stride in enumerate(_STRIDES):
            cls = cls_outs[stride_idx].reshape(-1)
            obj = obj_outs[stride_idx].reshape(-1)
            bbox = bbox_outs[stride_idx].reshape(-1, 4)

            score = np.sqrt(np.clip(cls, 0.0, 1.0) * np.clip(obj, 0.0, 1.0))
            mask = score >= threshold
            if not mask.any():
                continue

            cols = _INPUT_W // stride
            rows = _INPUT_H // stride
            r_grid, c_grid = np.meshgrid(
                np.arange(rows, dtype=np.float32),
                np.arange(cols, dtype=np.float32),
                indexing="ij",
            )
            r_flat = r_grid.reshape(-1)[mask]
            c_flat = c_grid.reshape(-1)[mask]
            bbox_m = bbox[mask]
            score_m = score[mask]

            cx = (c_flat + bbox_m[:, 0]) * stride
            cy = (r_flat + bbox_m[:, 1]) * stride
            w = np.exp(bbox_m[:, 2]) * stride
            h = np.exp(bbox_m[:, 3]) * stride
            x1 = cx - w / 2.0
            y1 = cy - h / 2.0

            all_boxes.append(np.stack([x1, y1, w, h], axis=1))
            all_scores.append(score_m)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not all_boxes:
            return DetectionResult([], orig_w, orig_h, elapsed_ms)

        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)

        keep = _nms(boxes, scores, self._nms_threshold)
        if not keep:
            return DetectionResult([], orig_w, orig_h, elapsed_ms)

        boxes = boxes[keep]
        scores = scores[keep]
        boxes_orig = boxes / scale

        faces: list[FaceBox] = []
        for (x, y, bw, bh), s in zip(boxes_orig, scores, strict=False):
            x_n = max(0.0, float(x) / orig_w)
            y_n = max(0.0, float(y) / orig_h)
            w_n = max(0.0, min(1.0 - x_n, float(bw) / orig_w))
            h_n = max(0.0, min(1.0 - y_n, float(bh) / orig_h))
            if w_n <= 0.0 or h_n <= 0.0:
                continue
            faces.append(
                FaceBox(x=x_n, y=y_n, w=w_n, h=h_n, confidence=float(s))
            )

        return DetectionResult(
            faces=faces,
            image_width=orig_w,
            image_height=orig_h,
            detection_ms=elapsed_ms,
        )


def _nms(
    boxes: np.ndarray, scores: np.ndarray, iou_threshold: float
) -> list[int]:
    if boxes.size == 0:
        return []
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = x1 + boxes[:, 2]
    y2 = y1 + boxes[:, 3]
    areas = boxes[:, 2] * boxes[:, 3]
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou < iou_threshold]
    return keep
