# GPhotos Face Detector

Tiny HTTP service that runs the bundled **YuNet** face detection model locally and returns bounding-box coordinates for photos sent by the [Google Photos Rotator](https://github.com/moimart/gphotos-integration) integration.

This add-on exists because the integration's in-process face detection backend (`onnxruntime`) doesn't have a Python 3.14 / musllinux wheel — so HA OS users can't run the model inside HA Core. This add-on sidesteps the problem by running the model in its own Debian-based container.

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add `https://github.com/moimart/gphotos-integration`.
3. Install **GPhotos Face Detector** from the new entry that appears.
4. Start the add-on.
5. In the Google Photos Rotator integration → **Configure** → enable face detection and (if not auto-detected) point the URL at `http://homeassistant.local:8127` or the add-on's internal hostname.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/v1/health` | `{"status": "ok", "model": "yunet-2023mar", "version": "..."}` |
| POST | `/v1/detect` | Body: raw image bytes (jpeg/png). Returns `{"faces": [...], "image_width": …, "image_height": …, "detection_ms": …}` with normalized 0–1 coords. |

Optional query param: `?min_confidence=0.5` overrides the default 0.6.

## Resource use

- Image size: ~250 MB on disk.
- RAM: ~80–120 MB when running.
- CPU: <0.5% sustained at the integration's default 60 s rotation cadence.

## Authentication

The add-on listens only on the HA-internal Docker network by default (no external port exposure). If you publish the port externally, set `GPHOTOS_DETECTOR_TOKEN` via the underlying image's env var support — currently requires running the standalone container rather than the add-on.
