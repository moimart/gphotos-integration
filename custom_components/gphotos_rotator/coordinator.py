"""Rotation coordinator for Google Photos Rotator."""
from __future__ import annotations

import logging
import random
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
    CONF_INTERVAL,
    CONF_MEDIA_ITEMS,
    CONF_ORDER,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_INTERVAL,
    DEFAULT_ORDER,
    DOMAIN,
    ORDER_RANDOM,
)

_LOGGER = logging.getLogger(__name__)


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

        self.current_index = index
        self.current_item = item
        self.current_bytes = data
        self.image_content_type = media_file.get("mimeType") or "image/jpeg"
        self.image_last_updated = dt_util.utcnow()

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
