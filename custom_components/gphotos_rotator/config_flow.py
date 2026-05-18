"""Config flow: OAuth2 + Google Photos Picker session."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.util import dt as dt_util

from .api import PickerApiError, PickerClient
from .const import (
    CONF_MEDIA_ITEMS,
    DEFAULT_INTERVAL,
    DEFAULT_ORDER,
    DOMAIN,
    OAUTH2_SCOPES,
)

_LOGGER = logging.getLogger(__name__)


class GPhotosOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """OAuth2 config flow for Google Photos Rotator."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._oauth_data: dict[str, Any] | None = None
        self._session_id: str | None = None
        self._picker_uri: str | None = None
        self._title: str = "Google Photos Rotator"

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        return {
            "scope": " ".join(OAUTH2_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._oauth_data = data
        return await self.async_step_pick_media()

    async def async_step_pick_media(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._oauth_data is not None
        client = self._build_client(self._oauth_data)

        if self._session_id is None:
            try:
                session = await client.create_session()
            except PickerApiError as err:
                _LOGGER.error("Failed to create picker session: %s", err)
                return self.async_abort(reason="picker_session_failed")
            self._session_id = session["id"]
            self._picker_uri = session["pickerUri"]

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                session = await client.get_session(self._session_id)
            except PickerApiError as err:
                _LOGGER.error("Picker session poll failed: %s", err)
                errors["base"] = "picker_session_failed"
            else:
                if not session.get("mediaItemsSet"):
                    errors["base"] = "picker_not_ready"
                else:
                    return await self._finish(client)

        return self.async_show_form(
            step_id="pick_media",
            data_schema=vol.Schema({}),
            description_placeholders={
                "picker_uri": self._picker_uri or "",
            },
            errors=errors,
        )

    async def _finish(self, client: PickerClient) -> ConfigFlowResult:
        assert self._session_id is not None
        assert self._oauth_data is not None
        try:
            items = await client.list_media_items(self._session_id)
        except PickerApiError as err:
            _LOGGER.error("Failed to list picked items: %s", err)
            return self.async_abort(reason="list_items_failed")

        if not items:
            return self.async_abort(reason="no_items_picked")

        data = {
            **self._oauth_data,
            "session_id": self._session_id,
            CONF_MEDIA_ITEMS: items,
            "items_fetched_at": dt_util.utcnow().isoformat(),
        }
        options = {
            "interval": DEFAULT_INTERVAL,
            "order": DEFAULT_ORDER,
        }
        return self.async_create_entry(
            title=self._title,
            data=data,
            options=options,
        )

    def _build_client(self, oauth_data: dict[str, Any]) -> PickerClient:
        implementation = self.flow_impl
        # Build a one-off OAuth2Session-like wrapper for the in-progress flow.
        session = _FlowOAuthSession(self.hass, implementation, oauth_data)
        return PickerClient(session)


class _FlowOAuthSession:
    """Minimal OAuth2Session shim used during the config flow.

    The real OAuth2Session needs a ConfigEntry, which doesn't exist yet during
    the flow. This shim provides the only method PickerClient calls
    (async_request) backed by a manual token refresh through the flow's
    implementation object.
    """

    def __init__(
        self,
        hass,
        implementation: config_entry_oauth2_flow.AbstractOAuth2Implementation,
        oauth_data: dict[str, Any],
    ) -> None:
        self.hass = hass
        self._impl = implementation
        self._token: dict[str, Any] = oauth_data["token"]

    @property
    def token(self) -> dict[str, Any]:
        return self._token

    async def async_ensure_token_valid(self) -> None:
        expires_at = self._token.get("expires_at")
        if expires_at is None or dt_util.utcnow().timestamp() < expires_at - 30:
            return
        self._token = await self._impl.async_refresh_token(self._token)

    async def async_request(self, method: str, url: str, **kwargs: Any):
        from homeassistant.helpers import aiohttp_client

        await self.async_ensure_token_valid()
        headers = kwargs.pop("headers", {}) or {}
        headers = {**headers, "Authorization": f"Bearer {self._token['access_token']}"}
        session = aiohttp_client.async_get_clientsession(self.hass)
        return await session.request(method, url, headers=headers, **kwargs)
