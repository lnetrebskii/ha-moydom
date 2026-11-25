"""Camera entity streaming the intercom video."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.exceptions import HomeAssistantError

from .api import MoydomError
from .const import DOMAIN, SNAPSHOT_HEIGHT, SNAPSHOT_WIDTH, STREAM_KEY_DATA, STREAM_PATH
from .entity import MoydomEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import MoydomConfigEntry
    from .coordinator import MoydomCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: MoydomConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a camera for each intercom that provides video."""
    coordinator = entry.runtime_data
    async_add_entities(
        MoydomCamera(coordinator, control.control_id)
        for control in coordinator.data.values()
        if control.allow_video and control.camera_id is not None
    )


class MoydomCamera(MoydomEntity, Camera):
    """Live video and snapshots of an intercom."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: MoydomCoordinator, control_id: int) -> None:
        """Create the camera."""
        MoydomEntity.__init__(self, coordinator, control_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{control_id}_camera"

    async def stream_source(self) -> str | None:
        """Return the local relay address; it fetches a fresh cloud URL on every open."""
        if self.control.camera_id is None:
            return None
        api = self.hass.config.api
        port = api.port if api is not None and api.port else 8123
        key = self.hass.data[DOMAIN][STREAM_KEY_DATA]
        return f"http://127.0.0.1:{port}{STREAM_PATH}/{self.control.control_id}?key={key}"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a current JPEG frame."""
        try:
            return await self.coordinator.api.async_snapshot(
                self.control, width or SNAPSHOT_WIDTH, height or SNAPSHOT_HEIGHT
            )
        except MoydomError as err:
            msg = f"Cannot get a frame of {self.control.name}: {err}"
            raise HomeAssistantError(msg) from err
