"""Expose the intercom cameras to indoor monitors as ONVIF cameras.

A monitor that searches the network for IP cameras finds each intercom through
WS-Discovery, asks it for its profile over ONVIF and then opens the RTSP address,
which go2rtc serves from the light (960x528) stream of the local relay.
"""

from __future__ import annotations

from functools import partial
import logging
from typing import TYPE_CHECKING

from homeassistant.components.network import async_get_source_ip
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_GO2RTC_URL,
    CONF_ONVIF,
    CONF_RTSP_PORT,
    DEFAULT_GO2RTC_URL,
    DEFAULT_ONVIF,
    DEFAULT_RTSP_PORT,
    DOMAIN,
    STREAM_KEY_DATA,
)
from .discovery import GROUP, DiscoveryDevice, DiscoveryResponder, async_start
from .go2rtc import Go2rtcClient, Go2rtcError
from .onvif import DEVICE_SERVICE, OnvifCamera, service_url
from .stream_proxy import relay_url

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from . import MoydomConfigEntry

_LOGGER = logging.getLogger(__name__)

RETRY_SECONDS = 60


def stream_name(control_id: int) -> str:
    """Return the go2rtc stream name of the light stream of an intercom."""
    return f"moydom_{control_id}_light"


class MonitorBridge:
    """Keep the ONVIF cameras, their RTSP streams and the discovery responder in step."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the bridge shared by all config entries."""
        self._hass = hass
        self.cameras: dict[int, OnvifCamera] = {}
        self._owners: dict[int, str] = {}
        self._responder: DiscoveryResponder | None = None
        self._address = ""
        self._cancel_retry: Callable[[], None] | None = None

    def _port(self) -> int:
        api = self._hass.config.api
        return api.port if api is not None and api.port else 8123

    def _devices(self) -> list[DiscoveryDevice]:
        base = f"http://{self._address}:{self._port()}"
        return [
            DiscoveryDevice(
                key=str(camera.control_id),
                name=camera.name,
                xaddr=service_url(base, camera.control_id, DEVICE_SERVICE),
            )
            for camera in self.cameras.values()
        ]

    async def async_sync(self, entry: MoydomConfigEntry) -> None:
        """Publish the intercoms of an entry, or withdraw them when the option is off."""
        if not entry.options.get(CONF_ONVIF, DEFAULT_ONVIF):
            await self.async_detach(entry)
            return
        address = await async_get_source_ip(self._hass, GROUP)
        if address is None:
            _LOGGER.warning("No network address found, intercoms are not shown to monitors")
            return
        self._address = address
        rtsp_port = entry.options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
        go2rtc = Go2rtcClient(
            async_get_clientsession(self._hass),
            entry.options.get(CONF_GO2RTC_URL, DEFAULT_GO2RTC_URL),
        )
        key = self._hass.data[DOMAIN][STREAM_KEY_DATA]
        failed = False
        for control in entry.runtime_data.data.values():
            if not control.allow_video or control.camera_id is None:
                continue
            name = stream_name(control.control_id)
            try:
                await go2rtc.async_ensure(
                    name, relay_url(self._port(), control.control_id, key, light=True)
                )
            except Go2rtcError as err:
                _LOGGER.warning("The monitor stream of %s is not available: %s", control.name, err)
                failed = True
                continue
            self.cameras[control.control_id] = OnvifCamera(
                control.control_id, control.name, f"rtsp://{address}:{rtsp_port}/{name}"
            )
            self._owners[control.control_id] = entry.entry_id
        await self._async_ensure_responder()
        self._schedule_retry(entry, failed=failed)

    def _schedule_retry(self, entry: MoydomConfigEntry, *, failed: bool) -> None:
        """Try again soon when go2rtc was not ready, e.g. it starts after Home Assistant."""
        self._stop_retry()
        if failed:
            self._cancel_retry = async_call_later(
                self._hass, RETRY_SECONDS, partial(self._retry, entry)
            )

    def _stop_retry(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    def _retry(self, entry: MoydomConfigEntry, _now: datetime) -> None:
        self._cancel_retry = None
        entry.async_create_background_task(
            self._hass, self.async_sync(entry), f"{DOMAIN}_monitor_retry"
        )

    async def _async_ensure_responder(self) -> None:
        if self._responder is None and self.cameras:
            try:
                self._responder = await async_start(self._address, self._devices)
            except OSError as err:
                _LOGGER.warning("Monitors cannot discover the intercoms: %s", err)

    async def async_detach(self, entry: MoydomConfigEntry) -> None:
        """Withdraw the intercoms of an entry."""
        self._stop_retry()
        go2rtc = Go2rtcClient(
            async_get_clientsession(self._hass),
            entry.options.get(CONF_GO2RTC_URL, DEFAULT_GO2RTC_URL),
        )
        for control_id in [c for c, owner in self._owners.items() if owner == entry.entry_id]:
            await go2rtc.async_unpublish(stream_name(control_id))
            self.cameras.pop(control_id, None)
            del self._owners[control_id]
        if not self.cameras and self._responder is not None:
            self._responder.close()
            self._responder = None
