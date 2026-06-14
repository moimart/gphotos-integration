"""Rotation coordinator for Google Photos Rotator."""
from __future__ import annotations

import logging
import os
import random
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import AuthError, PickerApiError, PickerClient, SessionExpiredError
from .const import (
    BASE_URL_TTL_SECONDS,
    CONF_FACE_DETECTION,
    CONF_FACE_MAX_DIMENSION,
    CONF_FACE_MIN_CONFIDENCE,
    CONF_INTERVAL,
    CONF_MEDIA_ITEMS,
    CONF_ORDER,
    DEFAULT_FACE_DETECTION,
    DEFAULT_FACE_MAX_DIMENSION,
    DEFAULT_FACE_MIN_CONFIDENCE,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_INTERVAL,
    DEFAULT_ORDER,
    DOMAIN,
    FACE_CACHE_MAX,
    FACE_MODEL_FILENAME,
    ORDER_RANDOM,
)

_LOGGER = logging.getLogger(__name__)


class _FaceDetectorUnavailable(RuntimeError):
    """Raised when cv2 (and therefore .face_detection) can't be imported."""


def _items_fetched_at_from_entry(entry: ConfigEntry) -> datetime:
    raw = entry.data.get("items_fetched_at")
    if raw:
        parsed = dt_util.parse_datetime(raw)
        if parsed:
            return parsed
    return dt_util.utcnow()


class GPhotosCoordinator(DataUpdateCoordinator[None]):
    """Picks a new media item on each tick and refreshes its bytes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PickerClient,
    ) -> None:
        self.entry = entry
        self.client = client
        self.session_id: str = entry.data["session_id"]
        self.media_items: list[dict[str, Any]] = list(
            entry.data.get(CONF_MEDIA_ITEMS, [])
        )
        self.items_fetched_at: datetime = _items_fetched_at_from_entry(entry)
        self.interval: int = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        self.order: str = entry.options.get(CONF_ORDER, DEFAULT_ORDER)
        self.current_index: int = -1
        self.current_item: dict[str, Any] | None = None
        self.current_bytes: bytes | None = None
        self.image_last_updated: datetime | None = None
        self.image_content_type: str = "image/jpeg"
        self._session_dead: bool = False

        # Face detection state. detector is lazy-built on first enabled use.
        self.face_detection_enabled: bool = entry.options.get(
            CONF_FACE_DETECTION, DEFAULT_FACE_DETECTION
        )
        self.face_min_confidence: float = entry.options.get(
            CONF_FACE_MIN_CONFIDENCE, DEFAULT_FACE_MIN_CONFIDENCE
        )
        self.face_max_dimension: int = entry.options.get(
            CONF_FACE_MAX_DIMENSION, DEFAULT_FACE_MAX_DIMENSION
        )
        self.current_faces: list[dict[str, float]] = []
        self.faces_detection_pending: bool = False
        self.faces_detection_ms: int = 0
        self.faces_image_width: int = 0
        self.faces_image_height: int = 0
        self._face_detector: Any | None = None
        self._face_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(seconds=self.interval),
        )

    async def _async_update_data(self) -> None:
        if not self.media_items:
            raise UpdateFailed("No picked media items")
        if self._session_dead:
            return
        await self._rotate()

    async def async_rotate_now(self) -> None:
        if self._session_dead:
            return
        await self._rotate()
        self.async_update_listeners()

    async def _rotate(self) -> None:
        await self._ensure_fresh_base_urls()
        if self._session_dead:
            return

        index = self._pick_next_index()
        item = self.media_items[index]
        media_file = item.get("mediaFile", {})
        base_url = media_file.get("baseUrl")
        if not base_url:
            raise UpdateFailed(f"Item {item.get('id')} missing baseUrl")

        try:
            data = await self.client.download_bytes(base_url, DEFAULT_IMAGE_SIZE)
        except AuthError as err:
            raise ConfigEntryAuthFailed(
                "Google rejected the OAuth token"
            ) from err
        except PickerApiError:
            self._mark_session_dead()
            return

        # Atomic swap: new image + cleared faces in a single update so any
        # consumer reading the faces sensor never sees coordinates from the
        # previous photo paired with the new image.
        self.current_index = index
        self.current_item = item
        self.current_bytes = data
        self.current_faces = []
        self.faces_detection_pending = self.face_detection_enabled
        self.faces_detection_ms = 0
        self.faces_image_width = 0
        self.faces_image_height = 0
        self.image_content_type = media_file.get("mimeType") or "image/jpeg"
        self.image_last_updated = dt_util.utcnow()
        self.async_update_listeners()

        # Detection runs after the image is published so the slow path
        # never blocks the rotation.
        if self.face_detection_enabled:
            await self._run_face_detection(item.get("id") or "", data)
            self.async_update_listeners()

    def _pick_next_index(self) -> int:
        count = len(self.media_items)
        if self.order == ORDER_RANDOM and count > 1:
            choices = [i for i in range(count) if i != self.current_index]
            return random.choice(choices)
        return (self.current_index + 1) % count

    async def _ensure_fresh_base_urls(self) -> None:
        age = (dt_util.utcnow() - self.items_fetched_at).total_seconds()
        if age < BASE_URL_TTL_SECONDS:
            return
        try:
            items = await self.client.list_media_items(self.session_id)
        except (SessionExpiredError, PickerApiError) as err:
            # AuthError (401/403) is a subclass of PickerApiError and is
            # caught here intentionally. The token was already validated in
            # async_setup_entry; a 401 at this point means the *session* is
            # dead (it was created with the old, now-revoked token), not
            # that the current token is bad.
            _LOGGER.warning(
                "Cannot refresh media list (%s); session likely expired", err
            )
            self._mark_session_dead()
            return

        if not items:
            self._mark_session_dead()
            return

        self.media_items = items
        self.items_fetched_at = dt_util.utcnow()
        self._persist_items()

    def _persist_items(self) -> None:
        new_data = {
            **self.entry.data,
            CONF_MEDIA_ITEMS: self.media_items,
            "items_fetched_at": self.items_fetched_at.isoformat(),
        }
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    def _mark_session_dead(self) -> None:
        if self._session_dead:
            return
        self._session_dead = True
        _LOGGER.warning("Picker session is dead; rotation paused until re-pick")
        persistent_notification.async_create(
            self.hass,
            (
                "Your Google Photos picker session has expired. "
                "Go to **[Settings → Devices & services]"
                "(/config/integrations/integration/" + DOMAIN + ")** "
                f"and press **Configure** on **{self.entry.title}** to "
                "select photos again.\n\n"
                "Photo rotation is paused until you re-pick."
            ),
            title="Google Photos Rotator — Session expired",
            notification_id=f"{DOMAIN}_session_expired_{self.entry.entry_id}",
        )

    # ----- interval / order management -----

    async def async_set_interval(self, seconds: int) -> None:
        self.interval = seconds
        self.update_interval = timedelta(seconds=seconds)
        new_options = {**self.entry.options, CONF_INTERVAL: seconds}
        self.hass.config_entries.async_update_entry(
            self.entry, options=new_options
        )
        # Reschedule the timer with the new interval.
        self._schedule_refresh()

    # ----- face detection -----

    async def _run_face_detection(self, media_id: str, data: bytes) -> None:
        cached = self._face_cache.get(media_id)
        if cached is not None:
            self._face_cache.move_to_end(media_id)
            self.current_faces = cached["faces"]
            self.faces_detection_ms = cached["detection_ms"]
            self.faces_image_width = cached["image_width"]
            self.faces_image_height = cached["image_height"]
            self.faces_detection_pending = False
            return

        try:
            detector = await self._ensure_face_detector()
        except _FaceDetectorUnavailable as err:
            _LOGGER.warning(
                "Face detector unavailable (%s); disabling for this session", err
            )
            self.face_detection_enabled = False
            self.faces_detection_pending = False
            return
        except Exception as err:  # noqa: BLE001 — model load errors vary
            _LOGGER.warning("Face detector init failed: %s", err)
            self.face_detection_enabled = False
            self.faces_detection_pending = False
            return

        try:
            result = await self.hass.async_add_executor_job(detector.detect, data)
        except Exception:  # noqa: BLE001 — defensive: any cv2 error
            _LOGGER.exception("Face detection raised")
            self.faces_detection_pending = False
            return

        faces = [f.as_dict() for f in result.faces]
        self.current_faces = faces
        self.faces_detection_ms = result.detection_ms
        self.faces_image_width = result.image_width
        self.faces_image_height = result.image_height
        self.faces_detection_pending = False

        if media_id:
            self._face_cache[media_id] = {
                "faces": faces,
                "detection_ms": result.detection_ms,
                "image_width": result.image_width,
                "image_height": result.image_height,
            }
            while len(self._face_cache) > FACE_CACHE_MAX:
                self._face_cache.popitem(last=False)

    async def _ensure_face_detector(self) -> Any:
        if self._face_detector is not None:
            return self._face_detector

        def _build() -> Any:
            try:
                from .face_detection import FaceDetector
            except ImportError as err:
                # Most likely cv2 not installed — Python 3.14 has no opencv
                # wheel yet (HA 2026.5+). Tell the user via notification and
                # disable the feature.
                raise _FaceDetectorUnavailable(str(err)) from err

            model_path = os.path.join(
                os.path.dirname(__file__), "models", FACE_MODEL_FILENAME
            )
            return FaceDetector(
                model_path=model_path,
                min_confidence=self.face_min_confidence,
                max_dimension=self.face_max_dimension,
            )

        try:
            self._face_detector = await self.hass.async_add_executor_job(_build)
        except _FaceDetectorUnavailable as err:
            self._notify_face_detector_missing(str(err))
            raise
        return self._face_detector

    def _notify_face_detector_missing(self, detail: str) -> None:
        persistent_notification.async_create(
            self.hass,
            (
                "Face detection requires the `opencv-python-headless` Python "
                "package, which currently has no wheel for Python 3.14 "
                "(shipped with Home Assistant 2026.5+).\n\n"
                "Face detection has been disabled. Either:\n"
                "- Wait for an upstream OpenCV wheel for Python 3.14, or\n"
                "- Wait for the integration's v0.3.x release which will "
                "switch to onnxruntime (already has Python 3.14 wheels).\n\n"
                f"Details: `{detail}`"
            ),
            title="Google Photos Rotator — face detection unavailable",
            notification_id=(
                f"{DOMAIN}_face_detector_missing_{self.entry.entry_id}"
            ),
        )

    async def async_apply_face_options(
        self,
        enabled: bool,
        min_confidence: float,
        max_dimension: int,
    ) -> None:
        prev_enabled = self.face_detection_enabled
        self.face_detection_enabled = enabled
        self.face_min_confidence = min_confidence
        self.face_max_dimension = max_dimension

        # Invalidate detector if thresholds or dimension changed; cache too.
        self._face_detector = None
        self._face_cache.clear()

        if not enabled:
            self.current_faces = []
            self.faces_detection_pending = False
            self.faces_detection_ms = 0
            self.faces_image_width = 0
            self.faces_image_height = 0
        elif prev_enabled != enabled and self.current_bytes and self.current_item:
            # Detection just turned on — analyze the current image now.
            self.faces_detection_pending = True
            self.async_update_listeners()
            await self._run_face_detection(
                self.current_item.get("id") or "", self.current_bytes
            )
        self.async_update_listeners()

    async def async_set_order(self, order: str) -> None:
        self.order = order
        new_options = {**self.entry.options, CONF_ORDER: order}
        self.hass.config_entries.async_update_entry(
            self.entry, options=new_options
        )

    # ----- repick flow -----

    async def async_start_repick(self) -> None:
        """Kick off the reconfigure flow so the user gets a proper modal.

        We can't pop a browser window from the backend, so the cleanest UX
        is to push the user into the integration's Configure dialog, which
        runs `async_step_reconfigure` — that step creates the picker session
        and shows its URL inside a normal HA flow modal.
        """
        from homeassistant.config_entries import SOURCE_RECONFIGURE

        self.hass.async_create_task(
            self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": SOURCE_RECONFIGURE,
                    "entry_id": self.entry.entry_id,
                    "title_placeholders": {"name": self.entry.title},
                },
            )
        )
        persistent_notification.async_create(
            self.hass,
            (
                "Open **[Settings → Devices & services]"
                "(/config/integrations/integration/" + DOMAIN + ")**, "
                f"find **{self.entry.title}**, and press **Configure** to "
                "select new photos."
            ),
            title="Google Photos Rotator — Re-pick photos",
            notification_id=f"{DOMAIN}_repick_{self.entry.entry_id}",
        )
