"""Constants for the Google Photos Rotator integration."""
from __future__ import annotations

DOMAIN = "gphotos_rotator"

OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"
OAUTH2_SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
]

PICKER_API_BASE = "https://photospicker.googleapis.com/v1"

CONF_MEDIA_ITEMS = "media_items"
CONF_INTERVAL = "interval"
CONF_ORDER = "order"

# Picker API quota is 10,000 requests/day per project. With one image
# download per tick + ~29 baseUrl-refresh list calls/day, intervals below
# 9s would exceed the quota.
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 9
MAX_INTERVAL = 86400

ORDER_RANDOM = "random"
ORDER_SEQUENTIAL = "sequential"
DEFAULT_ORDER = ORDER_RANDOM
ORDER_OPTIONS = [ORDER_RANDOM, ORDER_SEQUENTIAL]

# Picker mediaItem baseUrls expire ~60 min after issuance; refresh sooner.
BASE_URL_TTL_SECONDS = 50 * 60

# Default display size appended to baseUrl (=wW-hH).
DEFAULT_IMAGE_SIZE = "=w1920-h1080"

SERVICE_NEXT = "next"
SERVICE_REPICK = "repick"

# Face detection
CONF_FACE_DETECTION = "face_detection"
CONF_FACE_MIN_CONFIDENCE = "face_min_confidence"
CONF_FACE_MAX_DIMENSION = "face_max_dimension"
DEFAULT_FACE_DETECTION = False
DEFAULT_FACE_MIN_CONFIDENCE = 0.6
DEFAULT_FACE_MAX_DIMENSION = 1280
FACE_MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
FACE_CACHE_MAX = 200
