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

DEFAULT_INTERVAL = 60
MIN_INTERVAL = 5
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
