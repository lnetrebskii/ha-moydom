"""Data update coordinator listing the intercoms of the account."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthError, MoydomApi
from .const import DOMAIN, UPDATE_INTERVAL_MINUTES
from .models import AccessControl

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class MoydomCoordinator(DataUpdateCoordinator[dict[int, AccessControl]]):
    """Poll the list of access controls (intercoms) of every address."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: MoydomApi) -> None:
        """Create the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self.api = api

    async def _async_update_data(self) -> dict[int, AccessControl]:
        """Fetch all intercoms keyed by their id."""
        try:
            controls: dict[int, AccessControl] = {}
            for place in await self.api.async_get_places():
                for control in await self.api.async_get_access_controls(place.place_id):
                    controls[control.control_id] = control
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err
        return controls
