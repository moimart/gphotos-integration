"""Google Photos Rotator integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow, entity_registry as er
import voluptuous as vol

from .api import PickerApiError, PickerClient
from .const import DOMAIN, SERVICE_NEXT, SERVICE_REPICK
from .coordinator import GPhotosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SENSOR,
]


@dataclass
class GPhotosData:
    coordinator: GPhotosCoordinator
    client: PickerClient


type GPhotosConfigEntry = ConfigEntry[GPhotosData]

SERVICE_SCHEMA = vol.Schema({vol.Required("entity_id"): str})


async def async_setup_entry(
    hass: HomeAssistant, entry: GPhotosConfigEntry
) -> bool:
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, implementation
    )
    try:
        await oauth_session.async_ensure_token_valid()
    except PickerApiError as err:
        raise ConfigEntryNotReady(f"Token refresh failed: {err}") from err

    client = PickerClient(oauth_session)
    coordinator = GPhotosCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = GPhotosData(coordinator=coordinator, client=client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GPhotosConfigEntry
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _coord_for_entity(
    hass: HomeAssistant, entity_id: str
) -> GPhotosCoordinator | None:
    registry = er.async_get(hass)
    ent = registry.async_get(entity_id)
    if ent is None or ent.platform != DOMAIN:
        return None
    entry = hass.config_entries.async_get_entry(ent.config_entry_id)
    if entry is None or not hasattr(entry, "runtime_data"):
        return None
    return entry.runtime_data.coordinator


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_NEXT):
        return

    async def _next(call: ServiceCall) -> None:
        coord = _coord_for_entity(hass, call.data["entity_id"])
        if coord:
            await coord.async_rotate_now()

    async def _repick(call: ServiceCall) -> None:
        coord = _coord_for_entity(hass, call.data["entity_id"])
        if coord:
            await coord.async_start_repick()

    hass.services.async_register(DOMAIN, SERVICE_NEXT, _next, schema=SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REPICK, _repick, schema=SERVICE_SCHEMA
    )
