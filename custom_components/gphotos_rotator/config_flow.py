"""Config flow: OAuth2 + Google Photos Picker session."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from homeassistant.util import dt as dt_util

from .api import PickerApiError, PickerClient
from .const import (
    CONF_FACE_DETECTION,
    CONF_FACE_MAX_COUNT,
    CONF_FACE_MIN_AREA_FRACTION,
    CONF_FACE_MIN_CONFIDENCE,
    CONF_FACE_SERVICE_TOKEN,
    CONF_FACE_SERVICE_URL,
    CONF_MEDIA_ITEMS,
    DEFAULT_FACE_DETECTION,
    DEFAULT_FACE_MAX_COUNT,
    DEFAULT_FACE_MIN_AREA_FRACTION,
    DEFAULT_FACE_MIN_CONFIDENCE,
    DEFAULT_FACE_SERVICE_URL,
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GPhotosOptionsFlowHandler()

    def __init__(self) -> None:
        super().__init__()
        self._oauth_data: dict[str, Any] | None = None
        self._session_id: str | None = None
        self._picker_uri: str | None = None
        self._title: str = "Google Photos Rotator"
        self._entry_data: dict[str, Any] | None = None
        self._entry_options: dict[str, Any] | None = None

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
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            new_data = {**entry.data, **data}
            return self.async_update_reload_and_abort(
                entry, data=new_data, reason="reauth_successful"
            )
        self._oauth_data = data
        return await self.async_step_pick_media()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by HA when ConfigEntryAuthFailed is raised."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=vol.Schema({})
            )
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pick photos for an existing entry via the Configure button."""
        entry = self._get_reconfigure_entry()
        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    self.hass, entry
                )
            )
        except ValueError as err:
            _LOGGER.error("OAuth implementation missing: %s", err)
            return self.async_abort(reason="missing_configuration")
        oauth_session = config_entry_oauth2_flow.OAuth2Session(
            self.hass, entry, implementation
        )
        try:
            await oauth_session.async_ensure_token_valid()
        except Exception as err:  # noqa: BLE001 — token refresh errors vary
            # Dead refresh token (e.g. Testing-mode 7-day expiry). Re-run
            # the OAuth dance inside this reconfigure flow instead of
            # dead-ending; async_oauth_create_entry routes back to
            # pick_media and _finish persists the fresh token.
            _LOGGER.warning(
                "Token refresh failed during reconfigure (%s); "
                "starting re-authentication",
                err,
            )
            return await self.async_step_user()

        self.flow_impl = implementation
        self._oauth_data = {
            "token": oauth_session.token,
            "auth_implementation": entry.data.get("auth_implementation"),
        }
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

        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            # Free the superseded session server-side — Google caps active
            # picker sessions per user (RESOURCE_EXHAUSTED). Best-effort.
            old_session_id = entry.data.get("session_id")
            if old_session_id and old_session_id != self._session_id:
                await client.delete_session(old_session_id)
            # Merge _oauth_data too: if reconfigure went through a fresh
            # OAuth dance (dead token), this persists the new token.
            new_data = {
                **entry.data,
                **(self._oauth_data or {}),
                "session_id": self._session_id,
                CONF_MEDIA_ITEMS: items,
                "items_fetched_at": dt_util.utcnow().isoformat(),
            }
            return self.async_update_reload_and_abort(
                entry,
                data=new_data,
                reason="reconfigure_successful",
            )

        self._entry_data = {
            **self._oauth_data,
            "session_id": self._session_id,
            CONF_MEDIA_ITEMS: items,
            "items_fetched_at": dt_util.utcnow().isoformat(),
        }
        self._entry_options = {
            "interval": DEFAULT_INTERVAL,
            "order": DEFAULT_ORDER,
        }
        return await self.async_step_name()

    async def async_step_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the instance — the integration supports multiple entries
        (one photo selection each), so titles must be distinguishable."""
        assert self._entry_data is not None
        if user_input is not None:
            return self.async_create_entry(
                title=user_input["name"].strip() or self._title,
                data=self._entry_data,
                options=self._entry_options or {},
            )
        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema(
                {vol.Required("name", default=self._title): str}
            ),
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


class GPhotosOptionsFlowHandler(OptionsFlow):
    """Options flow: face detection toggle, service URL, thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FACE_DETECTION,
                        default=current.get(
                            CONF_FACE_DETECTION, DEFAULT_FACE_DETECTION
                        ),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FACE_SERVICE_URL,
                        default=current.get(
                            CONF_FACE_SERVICE_URL, DEFAULT_FACE_SERVICE_URL
                        ),
                    ): str,
                    vol.Optional(
                        CONF_FACE_SERVICE_TOKEN,
                        default=current.get(CONF_FACE_SERVICE_TOKEN, ""),
                    ): str,
                    vol.Required(
                        CONF_FACE_MIN_CONFIDENCE,
                        default=current.get(
                            CONF_FACE_MIN_CONFIDENCE, DEFAULT_FACE_MIN_CONFIDENCE
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.1, max=1.0, step=0.05, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_FACE_MIN_AREA_FRACTION,
                        default=current.get(
                            CONF_FACE_MIN_AREA_FRACTION,
                            DEFAULT_FACE_MIN_AREA_FRACTION,
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0.0, max=0.5, step=0.005, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_FACE_MAX_COUNT,
                        default=current.get(
                            CONF_FACE_MAX_COUNT, DEFAULT_FACE_MAX_COUNT
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=20, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )
