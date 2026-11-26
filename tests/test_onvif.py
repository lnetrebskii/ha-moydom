"""Tests of the ONVIF camera answers."""

from __future__ import annotations

import importlib
from xml.etree import ElementTree

import pytest

onvif = importlib.import_module("moydom_core.onvif")

CAMERA = onvif.OnvifCamera(9001, "Ленина 1 (п. 1)", "rtsp://192.168.1.55:8554/moydom_9001_light")
BASE = "http://192.168.1.55:8123"
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
}


def request(operation: str) -> str:
    """Return a request the way indoor monitors write it."""
    return (
        '<?xml version="1.0" encoding="utf-8"?><s:Envelope '
        'xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + operation
        + "</s:Body></s:Envelope>"
    )


def ask(operation: str) -> tuple[int, ElementTree.Element]:
    status, document = onvif.answer(CAMERA, BASE, request(operation))
    return status, ElementTree.fromstring(document)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            request('<GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>'),
            "GetDeviceInformation",
        ),
        (
            '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
            "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>"
            "</soap:Body></soap:Envelope>",
            "GetCapabilities",
        ),
        ("<Envelope><Header/><Body><GetProfiles/></Body></Envelope>", "GetProfiles"),
        ("garbage", None),
    ],
)
def test_request_action(body: str, expected: str | None) -> None:
    assert onvif.request_action(body) == expected


def test_capabilities_point_to_this_camera() -> None:
    status, root = ask('<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"/>')
    assert status == 200
    device = root.find(".//tt:Device/tt:XAddr", NS)
    media = root.find(".//tt:Media/tt:XAddr", NS)
    assert device is not None
    assert device.text == f"{BASE}/api/moydom/onvif/9001/device_service"
    assert media is not None
    assert media.text == f"{BASE}/api/moydom/onvif/9001/media_service"


def test_profile_describes_the_light_stream() -> None:
    status, root = ask('<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
    assert status == 200
    profiles = root.findall(".//trt:Profiles", NS)
    assert [p.get("token") for p in profiles] == ["Profile_1"]
    assert root.findtext(".//tt:VideoEncoderConfiguration/tt:Encoding", namespaces=NS) == "H264"
    assert root.findtext(".//tt:Resolution/tt:Width", namespaces=NS) == "960"
    assert root.findtext(".//tt:Resolution/tt:Height", namespaces=NS) == "528"


@pytest.mark.parametrize("token", ["", "Profile_1", "anything"])
def test_stream_uri_ignores_the_profile_token(token: str) -> None:
    status, root = ask(
        '<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl"><StreamSetup>'
        '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
        '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
        f"</StreamSetup><ProfileToken>{token}</ProfileToken></GetStreamUri>"
    )
    assert status == 200
    assert root.findtext(".//tt:Uri", namespaces=NS) == CAMERA.rtsp_url
    for required in ("InvalidAfterConnect", "InvalidAfterReboot", "Timeout"):
        assert root.find(f".//tt:{required}", NS) is not None


@pytest.mark.parametrize(
    "operation",
    [
        "GetSystemDateAndTime",
        "GetDeviceInformation",
        "GetServices",
        "GetVideoSources",
    ],
)
def test_other_operations_answer_well_formed_xml(operation: str) -> None:
    status, root = ask(f"<{operation}/>")
    assert status == 200
    assert root.find(".//s:Body", NS) is not None


def test_unknown_operation_is_a_soap_fault() -> None:
    status, root = ask("<SetHostname/>")
    assert status == 400
    assert root.find(".//s:Fault", NS) is not None


def test_names_are_escaped() -> None:
    camera = onvif.OnvifCamera(1, "x", "rtsp://h/a?b=1&c=<2>")
    _, document = onvif.answer(camera, BASE, request("<GetStreamUri/>"))
    assert ElementTree.fromstring(document).findtext(".//tt:Uri", namespaces=NS) == camera.rtsp_url
