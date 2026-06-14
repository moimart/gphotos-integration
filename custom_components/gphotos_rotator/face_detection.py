"""YuNet face detection wrapper.

This module is imported only when the user enables face detection in
options, so `import cv2` happens lazily — disabled users pay zero RAM cost.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

import cv2  # noqa: E402 — heavy import, deliberately at module level here

_LOGGER = logging.getLogger(__name__)


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
    """Lazy-init YuNet face detector. Single-threaded; call from executor."""

    def __init__(
        self,
        model_path: str,
        min_confidence: float = 0.6,
        max_dimension: int = 1280,
        nms_threshold: float = 0.3,
        top_k: int = 5000,
    ) -> None:
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._max_dimension = max_dimension
        self._nms_threshold = nms_threshold
        self._top_k = top_k
        # FaceDetectorYN takes input size at construction; we re-create per
        # image since photo aspect ratios vary.
        self._cache_size: tuple[int, int] | None = None
        self._detector: Any | None = None

    def _detector_for(self, width: int, height: int) -> Any:
        if self._cache_size == (width, height) and self._detector is not None:
            return self._detector
        self._detector = cv2.FaceDetectorYN.create(
            model=self._model_path,
            config="",
            input_size=(width, height),
            score_threshold=self._min_confidence,
            nms_threshold=self._nms_threshold,
            top_k=self._top_k,
        )
        self._cache_size = (width, height)
        return self._detector

    def detect(self, image_bytes: bytes) -> DetectionResult:
        start = time.monotonic()

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            _LOGGER.warning("Failed to decode image bytes for face detection")
            return DetectionResult([], 0, 0, 0)

        orig_h, orig_w = image.shape[:2]

        # Downscale if larger than max_dimension on the long edge — keeps
        # detection fast and bounded. Normalized coords come out identical
        # regardless of resize.
        long_edge = max(orig_w, orig_h)
        if long_edge > self._max_dimension:
            scale = self._max_dimension / long_edge
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            new_w, new_h = orig_w, orig_h

        detector = self._detector_for(new_w, new_h)
        _, faces = detector.detect(image)

        boxes: list[FaceBox] = []
        if faces is not None:
            for row in faces:
                # YuNet returns: x, y, w, h, 5 landmark coords (x,y x5), score
                x, y, w, h = row[0:4]
                score = float(row[-1])
                # Normalize against the *resized* dimensions — bbox is in
                # detection-image space, but normalized coords are identical
                # to the original since we did a pure scale.
                boxes.append(
                    FaceBox(
                        x=max(0.0, float(x) / new_w),
                        y=max(0.0, float(y) / new_h),
                        w=min(1.0, float(w) / new_w),
                        h=min(1.0, float(h) / new_h),
                        confidence=score,
                    )
                )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return DetectionResult(
            faces=boxes,
            image_width=orig_w,
            image_height=orig_h,
            detection_ms=elapsed_ms,
        )
