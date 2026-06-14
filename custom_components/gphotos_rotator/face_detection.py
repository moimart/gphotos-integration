"""HTTP client for the gphotos-face-detector service.

In v0.4.0 the face detection runtime moved out of HA Core (where the
onnxruntime / opencv wheels can't be installed on Alpine/musl + Python
3.14) and into a separate container — either the HA add-on
`gphotos_face_detector` or any other container running the same image.
This module just talks to it over HTTP.

The module is still imported lazily by `coordinator._ensure_face_detector`,
so users who don't enable face detection pay zero cost — same lazy-import
contract as previous versions.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

_HEALTH_TIMEOUT = ClientTimeout(total=5)
_DETECT_TIMEOUT = ClientTimeout(total=15)


class FaceServiceError(Exception):
    """Raised on any HTTP/transport error talking to the detector service."""


@dataclass(slots=True)
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    confidence: float

    def as_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class DetectionResult:
    faces: list[FaceBox]
    image_width: int
    image_height: int
    detection_ms: int


class FaceDetector:
    """HTTP client wrapping the detector service /v1/detect endpoint."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        min_confidence: float = 0.6,
        auth_token: str | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._min_confidence = float(min_confidence)
        self._auth_token = auth_token or None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def health(self) -> dict[str, Any]:
        url = f"{self._base_url}/v1/health"
        try:
            async with self._session.get(
                url, headers=self._headers(), timeout=_HEALTH_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise FaceServiceError(
                        f"GET {url} -> HTTP {resp.status}"
                    )
                return await resp.json()
        except ClientError as err:
            raise FaceServiceError(f"GET {url} failed: {err}") from err

    async def detect(self, image_bytes: bytes) -> DetectionResult:
        url = f"{self._base_url}/v1/detect"
        params = {"min_confidence": str(self._min_confidence)}
        start = time.monotonic()
        try:
            async with self._session.post(
                url,
                params=params,
                data=image_bytes,
                headers=self._headers("application/octet-stream"),
                timeout=_DETECT_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise FaceServiceError(
                        f"POST {url} -> HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()
        except ClientError as err:
            raise FaceServiceError(f"POST {url} failed: {err}") from err

        faces = [
            FaceBox(
                x=float(f["x"]),
                y=float(f["y"]),
                w=float(f["w"]),
                h=float(f["h"]),
                confidence=float(f["confidence"]),
            )
            for f in data.get("faces", [])
        ]

        # Trust the server's detection_ms (CPU-time) over our wall-clock —
        # ours would include the HTTP round-trip.
        elapsed_ms = int(data.get("detection_ms") or (time.monotonic() - start) * 1000)
        return DetectionResult(
            faces=faces,
            image_width=int(data.get("image_width", 0)),
            image_height=int(data.get("image_height", 0)),
            detection_ms=elapsed_ms,
        )
