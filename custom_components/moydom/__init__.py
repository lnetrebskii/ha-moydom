"""Мой Дом (Электронный город / Дом.ру): intercom video and door opener."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MoydomApi
from .const import (
    BRIDGE_DATA,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_INSTALLATION_ID,
    CONF_OPERATOR_ID,
    CONF_PLACE_ID,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    STREAM_KEY_DATA,
)
from .coordinator import MoydomCoordinator
from .models import Tokens
from .monitor import MonitorBridge
from .onvif_view import MoydomOnvifView
from .stream_proxy import MoydomStreamView, new_stream_key

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BUTTON, Platform.CAMERA]

type MoydomConfigEntry = ConfigEntry[MoydomCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the video relay and the ONVIF endpoint used by monitors."""
    hass.data[DOMAIN] = {STREAM_KEY_DATA: new_stream_key(), BRIDGE_DATA: MonitorBridge(hass)}
    hass.http.register_view(MoydomStreamView(hass))
    hass.http.register_view(MoydomOnvifView(hass))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MoydomConfigEntry) -> bool:
    """Set up the integration from a config entry."""

    async def store_tokens(tokens: Tokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tokens.access,
                CONF_REFRESH_TOKEN: tokens.refresh,
                CONF_OPERATOR_ID: tokens.operator_id,
            },
        )

    api = MoydomApi(
        async_get_clientsession(hass),
        entry.data[CONF_INSTALLATION_ID],
        Tokens(
            entry.data[CONF_ACCESS_TOKEN],
            entry.data[CONF_REFRESH_TOKEN],
            entry.data[CONF_OPERATOR_ID],
        ),
        account_id=entry.data[CONF_ACCOUNT_ID],
        place_id=entry.data[CONF_PLACE_ID],
        on_tokens=store_tokens,
    )
    coordinator = MoydomCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    bridge: MonitorBridge = hass.data[DOMAIN][BRIDGE_DATA]
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: entry.async_create_background_task(
                hass, bridge.async_sync(entry), f"{DOMAIN}_monitor_sync"
            )
        )
    )
    entry.async_create_background_task(hass, bridge.async_sync(entry), f"{DOMAIN}_monitor_setup")
    return True


async def _reload_on_options(hass: HomeAssistant, entry: MoydomConfigEntry) -> None:
    """Apply changed options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MoydomConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.data[DOMAIN][BRIDGE_DATA].async_detach(entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
