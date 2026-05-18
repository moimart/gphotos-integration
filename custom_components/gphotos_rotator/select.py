"""Order Select entity (random / sequential)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ORDER_OPTIONS
from .coordinator import GPhotosCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([OrderSelect(entry.runtime_data.coordinator, entry)])


class OrderSelect(CoordinatorEntity[GPhotosCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Order"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = ORDER_OPTIONS

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_order"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def current_option(self) -> str:
        return self.coordinator.order

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_order(option)
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
