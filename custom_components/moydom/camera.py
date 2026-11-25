"""Camera entity streaming the intercom video."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.exceptions import HomeAssistantError

from .api import MoydomError
from .const import SNAPSHOT_HEIGHT, SNAPSHOT_WIDTH
from .entity import MoydomEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import MoydomConfigEntry
    from .coordinator import MoydomCoordinator

_LOGGER = logging.getLogger(__name__)


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
        """Return a fresh stream URL; the server issues a new session per call."""
        camera_id = self.control.camera_id
        if camera_id is None:
            return None
        try:
            return await self.coordinator.api.async_video_url(camera_id)
        except MoydomError as err:
            _LOGGER.warning("Cannot get the stream URL of %s: %s", self.control.name, err)
            return None

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
