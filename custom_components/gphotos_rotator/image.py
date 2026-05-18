"""Image entity exposing the currently selected Google Photos picture."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GPhotosCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([NextPhotoImage(coordinator, entry)])


class NextPhotoImage(CoordinatorEntity[GPhotosCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_name = "Next photo"

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{entry.entry_id}_next_photo"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Google",
            "model": "Photos Picker",
            "entry_type": "service",
        }

    @property
    def content_type(self) -> str:
        return self.coordinator.image_content_type

    @property
    def image_last_updated(self) -> datetime | None:
        return self.coordinator.image_last_updated

    async def async_image(self) -> bytes | None:
        return self.coordinator.current_bytes

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        item = self.coordinator.current_item or {}
        media_file = item.get("mediaFile", {})
        return {
            "filename": media_file.get("filename"),
            "media_item_id": item.get("id"),
            "media_count": len(self.coordinator.media_items),
            "order": self.coordinator.order,
            "interval_seconds": self.coordinator.interval,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
