"""Base entity for the Мой Дом integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MoydomCoordinator
from .models import AccessControl


class MoydomEntity(CoordinatorEntity[MoydomCoordinator]):
    """An entity bound to one intercom."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MoydomCoordinator, control_id: int) -> None:
        """Bind the entity to the intercom with ``control_id``."""
        super().__init__(coordinator)
        self._control_id = control_id
        control = coordinator.data[control_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(control_id))},
            name=control.name,
            manufacturer="Электронный город / Дом.ру",
            model="Домофон",
        )

    @property
    def control(self) -> AccessControl:
        """Return the latest intercom data."""
        return self.coordinator.data[self._control_id]

    @property
    def available(self) -> bool:
        """Return False once the intercom disappeared from the account."""
        return super().available and self._control_id in self.coordinator.data
