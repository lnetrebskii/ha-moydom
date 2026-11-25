"""Button entities opening the intercom door."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .api import MoydomError
from .entity import MoydomEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import MoydomConfigEntry
    from .coordinator import MoydomCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: MoydomConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add an open button per intercom (or per entrance when it has several)."""
    coordinator = entry.runtime_data
    entities: list[MoydomOpenButton] = []
    for control in coordinator.data.values():
        if not control.allow_open:
            continue
        if control.entrance_ids:
            entities.extend(
                MoydomOpenButton(coordinator, control.control_id, entrance_id)
                for entrance_id in control.entrance_ids
            )
        else:
            entities.append(MoydomOpenButton(coordinator, control.control_id, None))
    async_add_entities(entities)


class MoydomOpenButton(MoydomEntity, ButtonEntity):
    """Open the door of an intercom."""

    _attr_icon = "mdi:door-open"

    def __init__(
        self, coordinator: MoydomCoordinator, control_id: int, entrance_id: int | None
    ) -> None:
        """Create the button."""
        super().__init__(coordinator, control_id)
        self._entrance_id = entrance_id
        suffix = "" if entrance_id is None else f"_{entrance_id}"
        self._attr_unique_id = f"{control_id}_open{suffix}"
        self._attr_translation_key = "open_door"
        if entrance_id is not None:
            self._attr_translation_key = "open_entrance"
            self._attr_translation_placeholders = {"entrance": str(entrance_id)}

    async def async_press(self) -> None:
        """Send the open command."""
        try:
            await self.coordinator.api.async_open_door(self.control, self._entrance_id)
        except MoydomError as err:
            msg = f"Cannot open the door: {err}"
            raise HomeAssistantError(msg) from err
