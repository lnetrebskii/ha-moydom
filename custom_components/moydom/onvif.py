"""Minimal ONVIF Profile S camera (device and media services) for one RTSP stream.

Indoor monitors that look for IP cameras on the network speak ONVIF: they ask for
the capabilities, the media profiles and the RTSP address of a profile. This module
answers those calls for an intercom whose video is published as RTSP elsewhere.
Every element the ONVIF schemas mark as required is present because strict
(generated) SOAP parsers reject answers that omit them.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from xml.sax.saxutils import escape

ONVIF_PATH = "/api/moydom/onvif"
DEVICE_SERVICE = "device_service"
MEDIA_SERVICE = "media_service"
MAX_BODY_BYTES = 65536

PROFILE_ID = "Profile_1"
VIDEO_WIDTH = 960
VIDEO_HEIGHT = 528
VIDEO_FPS = 25
VIDEO_KBPS = 1024

NS_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"

_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:tt="http://www.onvif.org/ver10/schema" '
    f'xmlns:tds="{NS_DEVICE}" xmlns:trt="{NS_MEDIA}"><s:Body>{{}}</s:Body></s:Envelope>'
)
_FAULT = (
    "<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode>"
    "<s:Value>ter:ActionNotSupported</s:Value></s:Subcode></s:Code>"
    '<s:Reason><s:Text xml:lang="en">Action not supported</s:Text></s:Reason></s:Fault>'
)
_MULTICAST = (
    "<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address>"
    "</tt:Address><tt:Port>0</tt:Port><tt:TTL>0</tt:TTL><tt:AutoStart>false</tt:AutoStart>"
    "</tt:Multicast>"
)
_PROFILE = (
    f'<trt:Profiles token="{PROFILE_ID}" fixed="true"><tt:Name>{PROFILE_ID}</tt:Name>'
    '<tt:VideoSourceConfiguration token="vsc_1"><tt:Name>VideoSource</tt:Name>'
    "<tt:UseCount>1</tt:UseCount><tt:SourceToken>vs_1</tt:SourceToken>"
    f'<tt:Bounds x="0" y="0" width="{VIDEO_WIDTH}" height="{VIDEO_HEIGHT}"/>'
    "</tt:VideoSourceConfiguration>"
    '<tt:VideoEncoderConfiguration token="vec_1"><tt:Name>H264</tt:Name>'
    "<tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding>"
    f"<tt:Resolution><tt:Width>{VIDEO_WIDTH}</tt:Width><tt:Height>{VIDEO_HEIGHT}</tt:Height>"
    "</tt:Resolution><tt:Quality>5</tt:Quality><tt:RateControl>"
    f"<tt:FrameRateLimit>{VIDEO_FPS}</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval>"
    f"<tt:BitrateLimit>{VIDEO_KBPS}</tt:BitrateLimit></tt:RateControl>"
    f"<tt:H264><tt:GovLength>{VIDEO_FPS}</tt:GovLength><tt:H264Profile>Main</tt:H264Profile>"
    f"</tt:H264>{_MULTICAST}<tt:SessionTimeout>PT60S</tt:SessionTimeout>"
    "</tt:VideoEncoderConfiguration></trt:Profiles>"
)
_ACTION = re.compile(r"<(?:[\w.-]+:)?(\w+)[\s/>]")


@dataclass(frozen=True, slots=True)
class OnvifCamera:
    """An intercom published as an ONVIF camera."""

    control_id: int
    name: str
    rtsp_url: str


def service_url(base_url: str, control_id: int, service: str) -> str:
    """Return the address of an ONVIF service of an intercom."""
    return f"{base_url}{ONVIF_PATH}/{control_id}/{service}"


def request_action(body: str) -> str | None:
    """Return the name of the SOAP operation in the body of a request."""
    start = re.search(r"<(?:[\w.-]+:)?Body[\s>]", body)
    if start is None:
        return None
    found = _ACTION.search(body, start.end())
    return found[1] if found else None


def _system_time() -> str:
    now = time.gmtime()
    return (
        "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>"
        "<tt:DateTimeType>NTP</tt:DateTimeType><tt:DaylightSavings>false</tt:DaylightSavings>"
        "<tt:TimeZone><tt:TZ>UTC0</tt:TZ></tt:TimeZone><tt:UTCDateTime><tt:Time>"
        f"<tt:Hour>{now.tm_hour}</tt:Hour><tt:Minute>{now.tm_min}</tt:Minute>"
        f"<tt:Second>{now.tm_sec}</tt:Second></tt:Time><tt:Date><tt:Year>{now.tm_year}</tt:Year>"
        f"<tt:Month>{now.tm_mon}</tt:Month><tt:Day>{now.tm_mday}</tt:Day></tt:Date>"
        "</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"
    )


def _device_information(camera: OnvifCamera) -> str:
    return (
        "<tds:GetDeviceInformationResponse><tds:Manufacturer>Home Assistant</tds:Manufacturer>"
        "<tds:Model>Intercom</tds:Model><tds:FirmwareVersion>1.0</tds:FirmwareVersion>"
        f"<tds:SerialNumber>moydom-{camera.control_id}</tds:SerialNumber>"
        "<tds:HardwareId>moydom</tds:HardwareId></tds:GetDeviceInformationResponse>"
    )


def _capabilities(device_url: str, media_url: str) -> str:
    return (
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>{device_url}</tt:XAddr></tt:Device>"
        f"<tt:Media><tt:XAddr>{media_url}</tt:XAddr><tt:StreamingCapabilities>"
        "<tt:RTPMulticast>false</tt:RTPMulticast><tt:RTP_TCP>true</tt:RTP_TCP>"
        "<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP></tt:StreamingCapabilities></tt:Media>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )


def _services(device_url: str, media_url: str) -> str:
    version = "<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
    return (
        "<tds:GetServicesResponse>"
        f"<tds:Service><tds:Namespace>{NS_DEVICE}</tds:Namespace>"
        f"<tds:XAddr>{device_url}</tds:XAddr>{version}</tds:Service>"
        f"<tds:Service><tds:Namespace>{NS_MEDIA}</tds:Namespace>"
        f"<tds:XAddr>{media_url}</tds:XAddr>{version}</tds:Service></tds:GetServicesResponse>"
    )


def _video_sources() -> str:
    return (
        '<trt:GetVideoSourcesResponse><trt:VideoSources token="vs_1">'
        f"<tt:Framerate>{VIDEO_FPS}</tt:Framerate><tt:Resolution><tt:Width>{VIDEO_WIDTH}</tt:Width>"
        f"<tt:Height>{VIDEO_HEIGHT}</tt:Height></tt:Resolution></trt:VideoSources>"
        "</trt:GetVideoSourcesResponse>"
    )


def _stream_uri(camera: OnvifCamera) -> str:
    return (
        f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>{escape(camera.rtsp_url)}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetStreamUriResponse>"
    )


def answer(camera: OnvifCamera, base_url: str, body: str) -> tuple[int, str]:
    """Return the HTTP status and SOAP document answering one request.

    The profile token of GetStreamUri is deliberately ignored: some monitors send
    it empty and the camera has a single profile anyway.
    """
    device_url = service_url(base_url, camera.control_id, DEVICE_SERVICE)
    media_url = service_url(base_url, camera.control_id, MEDIA_SERVICE)
    operations = {
        "GetSystemDateAndTime": _system_time,
        "GetDeviceInformation": lambda: _device_information(camera),
        "GetCapabilities": lambda: _capabilities(device_url, media_url),
        "GetServices": lambda: _services(device_url, media_url),
        "GetProfiles": lambda: f"<trt:GetProfilesResponse>{_PROFILE}</trt:GetProfilesResponse>",
        "GetVideoSources": _video_sources,
        "GetStreamUri": lambda: _stream_uri(camera),
    }
    build = operations.get(request_action(body) or "")
    if build is None:
        return 400, _ENVELOPE.format(_FAULT)
    return 200, _ENVELOPE.format(build())
