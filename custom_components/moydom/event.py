"""Doorbell events: one per visitor's call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventDeviceClass, EventEntity

from .entity import MoydomEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import MoydomConfigEntry
    from .coordinator import MoydomCoordinator
    from .sip import CallEvent

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant, entry: MoydomConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a doorbell event per intercom."""
    coordinator = entry.runtime_data
    async_add_entities(MoydomDoorbell(coordinator, control_id) for control_id in coordinator.data)


class MoydomDoorbell(MoydomEntity, EventEntity):
    """Fires ``ring`` when a visitor calls the apartment."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]  # noqa: RUF012
    _attr_translation_key = "doorbell"

    def __init__(self, coordinator: MoydomCoordinator, control_id: int) -> None:
        """Create the event entity."""
        super().__init__(coordinator, control_id)
        self._attr_unique_id = f"{control_id}_doorbell"

    async def async_added_to_hass(self) -> None:
        """Follow the calls."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.doorbell(self._control_id).add_listener(self._on_call)
        )

    def _on_call(self, event: CallEvent) -> None:
        if event.kind == "ring":
            self._trigger_event(EVENT_RING, {"caller": event.caller, "call_id": event.call_id})
            self.async_write_ha_state()
