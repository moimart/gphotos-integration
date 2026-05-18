"""Thin async client for the Google Photos Picker API."""
from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientResponseError

from homeassistant.helpers import config_entry_oauth2_flow

from .const import PICKER_API_BASE

_LOGGER = logging.getLogger(__name__)


class PickerApiError(Exception):
    """Generic Picker API error."""


class SessionExpiredError(PickerApiError):
    """Raised when the picker session is no longer valid."""


class PickerClient:
    """Wraps the Photos Picker REST endpoints using an HA OAuth2 session."""

    def __init__(
        self, oauth_session: config_entry_oauth2_flow.OAuth2Session
    ) -> None:
        self._session = oauth_session

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> Any:
        resp = await self._session.async_request(method, url, **kwargs)
        if resp.status == 404:
            raise SessionExpiredError(f"{method} {url} -> 404")
        if resp.status >= 400:
            text = await resp.text()
            raise PickerApiError(
                f"{method} {url} -> HTTP {resp.status}: {text[:200]}"
            )
        if resp.status == 204 or method == "DELETE":
            return None
        return await resp.json()

    async def create_session(self) -> dict[str, Any]:
        return await self._request("POST", f"{PICKER_API_BASE}/sessions", json={})

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{PICKER_API_BASE}/sessions/{session_id}"
        )

    async def list_media_items(self, session_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {"sessionId": session_id, "pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            data = await self._request(
                "GET", f"{PICKER_API_BASE}/mediaItems", params=params
            )
            items.extend(data.get("mediaItems", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items

    async def delete_session(self, session_id: str) -> None:
        try:
            await self._request(
                "DELETE", f"{PICKER_API_BASE}/sessions/{session_id}"
            )
        except (PickerApiError, ClientResponseError) as err:
            _LOGGER.debug("Ignoring delete_session failure: %s", err)

    async def download_bytes(self, base_url: str, size_suffix: str) -> bytes:
        url = f"{base_url}{size_suffix}"
        resp = await self._session.async_request("GET", url)
        if resp.status >= 400:
            raise PickerApiError(
                f"Download {url} -> HTTP {resp.status}"
            )
        return await resp.read()
