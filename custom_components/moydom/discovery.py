"""WS-Discovery responder: lets ONVIF clients on the network find the intercom cameras.

ONVIF clients (indoor monitors, NVRs) search with a multicast ``Probe`` to
239.255.255.250:3702 and connect to the addresses in the unicast ``ProbeMatches``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import socket
import struct
from typing import Any
import uuid
from xml.sax.saxutils import escape

_LOGGER = logging.getLogger(__name__)

GROUP = "239.255.255.250"
PORT = 3702
HELLO_INTERVAL = 60.0
_NAMESPACES = (
    'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"'
)
_ACTION = "http://schemas.xmlsoap.org/ws/2005/04/discovery/"
_ENDPOINT_NAMESPACE = uuid.UUID("6f1c1c58-3b64-4f6b-9b0e-0b5a8f1f4a11")


@dataclass(frozen=True, slots=True)
class DiscoveryDevice:
    """A camera announced on the network."""

    key: str
    name: str
    xaddr: str

    @property
    def urn(self) -> str:
        """Return the stable endpoint address of the device."""
        return f"urn:uuid:{uuid.uuid5(_ENDPOINT_NAMESPACE, self.key)}"


def _scopes(device: DiscoveryDevice) -> str:
    name = "".join(ch for ch in device.name if ch.isalnum()) or "Intercom"
    return (
        "onvif://www.onvif.org/type/video_encoder "
        "onvif://www.onvif.org/type/NetworkVideoTransmitter "
        "onvif://www.onvif.org/Profile/Streaming "
        f"onvif://www.onvif.org/name/{name} onvif://www.onvif.org/hardware/Intercom"
    )


def _envelope(action: str, to: str, relates: str | None, body: str) -> bytes:
    relation = f"<a:RelatesTo>{escape(relates)}</a:RelatesTo>" if relates else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope {_NAMESPACES}><s:Header>'
        f"<a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>{relation}"
        f"<a:To>{to}</a:To><a:Action>{_ACTION}{action}</a:Action></s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


def _match(device: DiscoveryDevice) -> str:
    return (
        f"<a:EndpointReference><a:Address>{device.urn}</a:Address></a:EndpointReference>"
        f"<d:Types>dn:NetworkVideoTransmitter</d:Types><d:Scopes>{_scopes(device)}</d:Scopes>"
        f"<d:XAddrs>{escape(device.xaddr)}</d:XAddrs><d:MetadataVersion>1</d:MetadataVersion>"
    )


def probe_matches(device: DiscoveryDevice, relates: str | None) -> bytes:
    """Return the unicast answer to a Probe."""
    body = f"<d:ProbeMatches><d:ProbeMatch>{_match(device)}</d:ProbeMatch></d:ProbeMatches>"
    return _envelope(
        "ProbeMatches",
        "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous",
        relates,
        body,
    )


def hello(device: DiscoveryDevice) -> bytes:
    """Return the multicast announcement of a device that came online."""
    return _envelope(
        "Hello",
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery",
        None,
        f"<d:Hello>{_match(device)}</d:Hello>",
    )


def bye(device: DiscoveryDevice) -> bytes:
    """Return the multicast announcement of a device that went away."""
    body = (
        f"<d:Bye><a:EndpointReference><a:Address>{device.urn}</a:Address>"
        "</a:EndpointReference></d:Bye>"
    )
    return _envelope("Bye", "urn:schemas-xmlsoap-org:ws:2005:04:discovery", None, body)


def message_id(data: bytes) -> str | None:
    """Return the MessageID of a request, to be echoed in ``RelatesTo``."""
    found = re.search(
        r"<(?:[\w.-]+:)?MessageID[^>]*>\s*([^<\s]+)\s*<", data.decode("utf-8", "replace")
    )
    return found[1] if found else None


def is_probe(data: bytes) -> bool:
    """Return True for a Probe request (not a ProbeMatches, Hello or Bye)."""
    text = data.decode("utf-8", "replace")
    return "discovery/Probe<" in text.replace(" ", "")


def create_socket(interface_ip: str) -> socket.socket:
    """Return a UDP socket joined to the discovery group on the given interface."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", PORT))
    membership = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(interface_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setblocking(False)
    return sock


class DiscoveryResponder(asyncio.DatagramProtocol):
    """Answer every Probe with one ProbeMatches per announced device."""

    def __init__(self, devices: Callable[[], list[DiscoveryDevice]]) -> None:
        """Create the responder; ``devices`` returns the cameras to announce."""
        self._devices = devices
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task[None] | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Announce the devices and keep repeating the announcement."""
        self._transport = transport  # type: ignore[assignment]
        self._task = asyncio.get_running_loop().create_task(self._announce())

    async def _announce(self) -> None:
        while True:
            for device in self._devices():
                self._send(hello(device), (GROUP, PORT))
            await asyncio.sleep(HELLO_INTERVAL)

    def _send(self, data: bytes, address: tuple[str, int]) -> None:
        if self._transport is not None:
            self._transport.sendto(data, address)

    def datagram_received(self, data: bytes, addr: Any) -> None:
        """Answer a Probe from a client."""
        if not is_probe(data):
            return
        relates = message_id(data)
        for device in self._devices():
            self._send(probe_matches(device, relates), (addr[0], addr[1]))
        _LOGGER.debug("Answered a discovery probe from %s", addr[0])

    def close(self) -> None:
        """Say goodbye and stop."""
        if self._task is not None:
            self._task.cancel()
        for device in self._devices():
            self._send(bye(device), (GROUP, PORT))
        if self._transport is not None:
            self._transport.close()
            self._transport = None


async def async_start(
    interface_ip: str, devices: Callable[[], list[DiscoveryDevice]]
) -> DiscoveryResponder:
    """Start answering discovery probes on the interface with ``interface_ip``."""
    loop = asyncio.get_running_loop()
    responder = DiscoveryResponder(devices)
    await loop.create_datagram_endpoint(lambda: responder, sock=create_socket(interface_ip))
    return responder
