"""FastAPI face detection service for Google Photos Rotator.

Endpoints:
- GET  /v1/health  -> {"status": "ok", "model": "yunet-2023mar"}
- POST /v1/detect  -> {"faces": [...], "image_width": ..., "image_height": ..., "detection_ms": ...}

Auth (optional, recommended for non-add-on deployments):
- If env var GPHOTOS_DETECTOR_TOKEN is set, requests must carry
  Authorization: Bearer <token>.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .detector import FaceDetector

_LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

MODEL_PATH = str(Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.4.0")
AUTH_TOKEN = os.getenv("GPHOTOS_DETECTOR_TOKEN", "")
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(25 * 1024 * 1024)))  # 25 MB

app = FastAPI(
    title="gphotos-face-detector",
    version=SERVICE_VERSION,
    description="Local face detection service for Google Photos Rotator (YuNet)",
)

_detector: FaceDetector | None = None


@app.on_event("startup")
def _startup() -> None:
    global _detector
    _LOGGER.info("Loading YuNet model from %s", MODEL_PATH)
    _detector = FaceDetector(model_path=MODEL_PATH)
    _LOGGER.info("Model ready. Auth: %s", "enabled" if AUTH_TOKEN else "disabled")


def _require_auth(authorization: str | None) -> None:
    if not AUTH_TOKEN:
        return
    expected = f"Bearer {AUTH_TOKEN}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "model": "yunet-2023mar",
        "version": SERVICE_VERSION,
    }


@app.post("/v1/detect")
async def detect(
    request: Request,
    authorization: str | None = Header(default=None),
    min_confidence: float | None = None,
) -> JSONResponse:
    _require_auth(authorization)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds {MAX_BODY_BYTES} bytes",
        )

    assert _detector is not None  # populated in startup
    result = _detector.detect(body, min_confidence=min_confidence)
    return JSONResponse(
        {
            "faces": [f.as_dict() for f in result.faces],
            "image_width": result.image_width,
            "image_height": result.image_height,
            "detection_ms": result.detection_ms,
            "detector": "yunet-2023mar",
        }
    )
