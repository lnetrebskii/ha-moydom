"""Мой Дом (Электронный город / Дом.ру): intercom video and door opener."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MoydomApi
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_INSTALLATION_ID,
    CONF_OPERATOR_ID,
    CONF_PLACE_ID,
    CONF_REFRESH_TOKEN,
)
from .coordinator import MoydomCoordinator
from .models import Tokens

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.BUTTON, Platform.CAMERA]

type MoydomConfigEntry = ConfigEntry[MoydomCoordinator]


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MoydomConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
