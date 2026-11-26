"""HTTP endpoint answering the ONVIF requests of monitors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import BRIDGE_DATA, DOMAIN
from .onvif import DEVICE_SERVICE, MAX_BODY_BYTES, MEDIA_SERVICE, ONVIF_PATH, answer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class MoydomOnvifView(HomeAssistantView):
    """Serve ``/api/moydom/onvif/{control_id}/{device_service|media_service}``.

    The monitor has no Home Assistant credentials, so the view is open; it only
    describes the camera and gives the address of its RTSP stream.
    """

    url = f"{ONVIF_PATH}/{{control_id}}/{{service}}"
    name = "api:moydom:onvif"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the view."""
        self._hass = hass

    async def post(self, request: web.Request, control_id: str, service: str) -> web.Response:
        """Answer one SOAP request."""
        camera = None
        if control_id.isdigit() and service in (DEVICE_SERVICE, MEDIA_SERVICE):
            camera = self._hass.data[DOMAIN][BRIDGE_DATA].cameras.get(int(control_id))
        if camera is None:
            raise web.HTTPNotFound
        data = await request.content.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=len(data))
        body = data.decode("utf-8", "replace")
        status, document = answer(camera, f"{request.scheme}://{request.host}", body)
        return web.Response(
            status=status, text=document, content_type="application/soap+xml", charset="utf-8"
        )
