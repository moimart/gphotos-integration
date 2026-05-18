"""Buttons: rotate now, re-pick photos."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
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
        [NextButton(coordinator, entry), RepickButton(coordinator, entry)]
    )


class _BaseButton(CoordinatorEntity[GPhotosCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GPhotosCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}


class NextButton(_BaseButton):
    _attr_name = "Next photo now"

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "next_button")

    async def async_press(self) -> None:
        await self.coordinator.async_rotate_now()


class RepickButton(_BaseButton):
    _attr_name = "Re-pick photos"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: GPhotosCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "repick_button")

    async def async_press(self) -> None:
        await self.coordinator.async_start_repick()
