"""Publish the light intercom stream through a go2rtc server.

go2rtc turns the relay's HTTP video into RTSP for ONVIF clients such as indoor
monitors. A monitor gives up when RTSP does not answer within about two seconds
and starting the cloud stream takes longer, so the stream is preloaded: go2rtc
keeps the source connected and answers at once.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)
PRELOAD_QUERY = "video"


class Go2rtcError(Exception):
    """go2rtc is unreachable or refused a request."""


class Go2rtcClient:
    """The small part of the go2rtc API this integration uses."""

    def __init__(self, session: ClientSession, url: str) -> None:
        """Create a client for the go2rtc API at ``url``."""
        self._session = session
        self._api = f"{url.rstrip('/')}/api"

    async def _call(self, method: str, path: str, params: dict[str, str]) -> Any:
        try:
            async with self._session.request(
                method, f"{self._api}/{path}", params=params, timeout=TIMEOUT
            ) as response:
                if response.status != 200:
                    detail = (await response.text()).strip()[:200]
                    msg = f"go2rtc {method} {path}: HTTP {response.status} {detail}"
                    raise Go2rtcError(msg)
                return await response.json(content_type=None) if method == "GET" else None
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            msg = f"go2rtc {method} {path}: {err}"
            raise Go2rtcError(msg) from err

    async def async_is_published(self, name: str, source: str) -> bool:
        """Return True when the stream exists with this source and is preloaded."""
        try:
            stream = await self._call("GET", "streams", {"src": name})
            preloaded = await self._call("GET", "preload", {})
        except Go2rtcError:
            return False
        producers = stream.get("producers") if isinstance(stream, dict) else None
        sources = [p.get("url") for p in producers or [] if isinstance(p, dict)]
        return sources == [source] and isinstance(preloaded, dict) and name in preloaded

    async def async_ensure(self, name: str, source: str) -> None:
        """Publish the stream unless it is already there; replacing it would cut viewers."""
        if not await self.async_is_published(name, source):
            await self.async_publish(name, source)

    async def async_publish(self, name: str, source: str) -> None:
        """Create or replace the stream ``name`` and keep it preloaded."""
        await self._call("PUT", "streams", {"name": name, "src": source})
        await self._call("PUT", "preload", {"src": name, "query": PRELOAD_QUERY})

    async def async_unpublish(self, name: str) -> None:
        """Stop preloading and delete the stream ``name``; missing ones are ignored."""
        for path in ("preload", "streams"):
            try:
                await self._call("DELETE", path, {"src": name})
            except Go2rtcError as err:
                _LOGGER.debug("Cannot remove %s from %s: %s", name, path, err)
