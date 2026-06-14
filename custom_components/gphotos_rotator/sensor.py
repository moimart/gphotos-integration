"""Diagnostic sensor: how many media items are in the rotation set."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    async_add_entities(
        [
            MediaCountSensor(coordinator, entry),
            FacesSensor(coordinator, entry),
        ]
    )


class MediaCountSensor(CoordinatorEntity[GPhotosCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Media count"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_media_count"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def native_value(self) -> int:
        return len(self.coordinator.media_items)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class FacesSensor(CoordinatorEntity[GPhotosCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Faces count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_faces_count"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.face_detection_enabled
        )

    @property
    def native_value(self) -> int:
        return len(self.coordinator.current_faces)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        item = self.coordinator.current_item or {}
        return {
            "faces": self.coordinator.current_faces,
            "image_width": self.coordinator.faces_image_width,
            "image_height": self.coordinator.faces_image_height,
            "detection_pending": self.coordinator.faces_detection_pending,
            "detection_ms": self.coordinator.faces_detection_ms,
            "detector": "yunet",
            "media_item_id": item.get("id"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
