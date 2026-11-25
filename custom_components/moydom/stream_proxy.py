"""Local relay of the intercom video.

The cloud issues a one-shot stream URL: a second request to the same URL fails
with an empty response. Home Assistant opens a source several times (go2rtc,
HLS worker, restarts), so the camera hands out the address of this relay, and
every request to it asks the cloud for a fresh URL and forwards the bytes as
they are, without transcoding.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MoydomError
from .const import DOMAIN, STREAM_KEY_DATA, STREAM_PATH

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import MoydomCoordinator

_LOGGER = logging.getLogger(__name__)

UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
DEFAULT_CONTENT_TYPE = "video/x-flv"


def new_stream_key() -> str:
    """Return a random key that authorises requests to the relay."""
    return secrets.token_urlsafe(32)


class MoydomStreamView(HomeAssistantView):
    """Relay ``/api/moydom/stream/{control_id}`` to a fresh cloud stream."""

    url = f"{STREAM_PATH}/{{control_id}}"
    name = "api:moydom:stream"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the view."""
        self._hass = hass

    def _find(self, control_id: int) -> tuple[MoydomCoordinator, int] | None:
        """Return the coordinator and camera id of an intercom."""
        for entry in self._hass.config_entries.async_loaded_entries(DOMAIN):
            control = entry.runtime_data.data.get(control_id)
            if control is not None and control.camera_id is not None:
                return entry.runtime_data, control.camera_id
        return None

    async def get(self, request: web.Request, control_id: str) -> web.StreamResponse:
        """Forward the live video."""
        expected = self._hass.data[DOMAIN][STREAM_KEY_DATA]
        if not hmac.compare_digest(request.query.get("key", ""), expected):
            raise web.HTTPUnauthorized
        found = self._find(int(control_id)) if control_id.isdigit() else None
        if found is None:
            raise web.HTTPNotFound
        coordinator, camera_id = found
        try:
            source = await coordinator.api.async_video_url(camera_id)
        except MoydomError as err:
            _LOGGER.warning("Cannot get the stream URL: %s", err)
            raise web.HTTPBadGateway from err
        return await self._relay(request, source)

    async def _relay(self, request: web.Request, source: str) -> web.StreamResponse:
        """Copy the cloud stream to the client until either side closes."""
        session = async_get_clientsession(self._hass)
        response: web.StreamResponse | None = None
        try:
            async with session.get(source, timeout=UPSTREAM_TIMEOUT) as upstream:
                if upstream.status != 200:
                    _LOGGER.warning("The video server answered HTTP %s", upstream.status)
                    raise web.HTTPBadGateway
                content_type = upstream.headers.get("Content-Type", DEFAULT_CONTENT_TYPE)
                response = web.StreamResponse(
                    headers={"Content-Type": content_type, "Cache-Control": "no-store"}
                )
                await response.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
        except (aiohttp.ClientError, TimeoutError, ConnectionResetError) as err:
            _LOGGER.debug("Video relay stopped: %s", err)
            if response is None:
                raise web.HTTPBadGateway from err
        return response
