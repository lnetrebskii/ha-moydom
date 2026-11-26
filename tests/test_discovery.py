"""Tests of the WS-Discovery responder."""

from __future__ import annotations

import importlib
from xml.etree import ElementTree

discovery = importlib.import_module("moydom_core.discovery")

DEVICE = discovery.DiscoveryDevice("9001", "Ленина 1 (п. 1)", "http://192.168.1.55:8123/x?a=1&b=2")
PROBE = (
    b'<?xml version="1.0" encoding="utf-8"?><Envelope xmlns:dn="http://www.onvif.org/ver10/network/wsdl" '
    b'xmlns="http://www.w3.org/2003/05/soap-envelope"><Header>'
    b'<wsa:MessageID xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">uuid:abc-1</wsa:MessageID>'
    b'<wsa:To xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
    b"urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>"
    b'<wsa:Action xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">'
    b"http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action></Header><Body>"
    b'<Probe xmlns="http://schemas.xmlsoap.org/ws/2005/04/discovery"><Types>dn:NetworkVideoTransmitter'
    b"</Types></Probe></Body></Envelope>"
)
NS = {
    "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
}


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


def test_probe_is_recognised() -> None:
    assert discovery.is_probe(PROBE)
    assert not discovery.is_probe(discovery.probe_matches(DEVICE, None))
    assert not discovery.is_probe(discovery.hello(DEVICE))
    assert not discovery.is_probe(discovery.bye(DEVICE))


def test_message_id() -> None:
    assert discovery.message_id(PROBE) == "uuid:abc-1"
    assert discovery.message_id(b"nothing") is None


def test_probe_matches_carries_the_address_and_relation() -> None:
    root = ElementTree.fromstring(discovery.probe_matches(DEVICE, "uuid:abc-1"))
    assert root.findtext(".//a:RelatesTo", namespaces=NS) == "uuid:abc-1"
    assert root.findtext(".//d:XAddrs", namespaces=NS) == DEVICE.xaddr
    assert "NetworkVideoTransmitter" in (root.findtext(".//d:Types", namespaces=NS) or "")
    assert root.findtext(".//a:Address", namespaces=NS) == DEVICE.urn


def test_endpoint_is_stable_and_distinct() -> None:
    other = discovery.DiscoveryDevice("9002", "x", "http://h")
    assert DEVICE.urn == discovery.DiscoveryDevice("9001", "y", "http://z").urn
    assert DEVICE.urn != other.urn


def test_responder_answers_each_device_to_the_asker() -> None:
    other = discovery.DiscoveryDevice("9002", "Двор", "http://192.168.1.55:8123/y")
    responder = discovery.DiscoveryResponder(lambda: [DEVICE, other])
    transport = FakeTransport()
    responder._transport = transport
    responder.datagram_received(PROBE, ("192.168.1.60", 8888))
    assert [addr for _, addr in transport.sent] == [("192.168.1.60", 8888)] * 2
    addresses = [
        ElementTree.fromstring(data).findtext(".//d:XAddrs", namespaces=NS)
        for data, _ in transport.sent
    ]
    assert addresses == [DEVICE.xaddr, other.xaddr]


def test_responder_ignores_other_traffic() -> None:
    responder = discovery.DiscoveryResponder(lambda: [DEVICE])
    transport = FakeTransport()
    responder._transport = transport
    responder.datagram_received(discovery.hello(DEVICE), ("192.168.1.9", 3702))
    responder.datagram_received(b"junk", ("192.168.1.9", 3702))
    assert transport.sent == []


def test_close_says_bye() -> None:
    responder = discovery.DiscoveryResponder(lambda: [DEVICE])
    transport = FakeTransport()
    responder._transport = transport
    responder.close()
    assert transport.closed
    assert len(transport.sent) == 1
    assert b"Bye" in transport.sent[0][0]
