"""Binary sensors: somebody is calling from the intercom."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import MoydomEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import MoydomConfigEntry
    from .coordinator import MoydomCoordinator
    from .sip import CallEvent


async def async_setup_entry(
    hass: HomeAssistant, entry: MoydomConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a call sensor per intercom."""
    coordinator = entry.runtime_data
    async_add_entities(MoydomCallSensor(coordinator, control_id) for control_id in coordinator.data)


class MoydomCallSensor(MoydomEntity, BinarySensorEntity):
    """On while a visitor's call is ringing."""

    _attr_translation_key = "call"
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator: MoydomCoordinator, control_id: int) -> None:
        """Create the sensor."""
        super().__init__(coordinator, control_id)
        self._attr_unique_id = f"{control_id}_call"

    @property
    def is_on(self) -> bool:
        """Return True while the call rings."""
        return self.coordinator.doorbell(self._control_id).ringing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return who called last and when."""
        last = self.coordinator.doorbell(self._control_id).last
        if last is None:
            return {}
        return {"caller": last.caller, "last_ring": last.started.isoformat()}

    async def async_added_to_hass(self) -> None:
        """Follow the calls."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.doorbell(self._control_id).add_listener(self._on_call)
        )

    def _on_call(self, _event: CallEvent) -> None:
        self.async_write_ha_state()
