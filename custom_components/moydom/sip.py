"""A minimal SIP user agent that rings when somebody calls the apartment.

The intercom server treats every registered device of the subscriber as a phone:
a visitor's call arrives as an ``INVITE``. The agent registers, answers the
``INVITE`` with ``100 Trying`` and ``180 Ringing`` only (it never picks up, so
the call stays free for the phone and the monitor) and reports when the call
starts and ends. The protocol logic is separate from the socket so it can be tested.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import logging
import re
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_LOGGER = logging.getLogger(__name__)

SIP_PORT = 5060
REGISTER_EXPIRES = 60
MAX_DATAGRAM_BYTES = 8192
_PARAMS = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]+))')
_PEER_METHODS = frozenset({"OPTIONS", "BYE", "NOTIFY", "INFO", "MESSAGE", "UPDATE"})
_CALL_METHODS = frozenset({"INVITE", "CANCEL", "BYE"})


@dataclass(frozen=True, slots=True)
class SipAccount:
    """The SIP credentials the intercom cloud issues to a device."""

    login: str
    password: str
    realm: str


@dataclass(frozen=True, slots=True)
class SipMessage:
    """A parsed SIP request or response."""

    start_line: str
    headers: tuple[tuple[str, str], ...]
    body: str

    def header(self, name: str) -> str:
        """Return the first value of a header, or an empty string."""
        return next(self.all(name), "")

    def all(self, name: str) -> Iterator[str]:
        """Iterate over every value of a header."""
        wanted = name.lower()
        return (value for key, value in self.headers if key == wanted)

    @property
    def method(self) -> str:
        """Return the method of a request, or an empty string for a response."""
        return "" if self.start_line.startswith("SIP/2.0") else self.start_line.split(" ", 1)[0]

    @property
    def status(self) -> int:
        """Return the status code of a response, or 0 for a request."""
        parts = self.start_line.split(" ", 2)
        return int(parts[1]) if self.start_line.startswith("SIP/2.0") and parts[1].isdigit() else 0

    @property
    def call_id(self) -> str:
        """Return the Call-ID."""
        return self.header("call-id")


def parse(data: bytes) -> SipMessage | None:
    """Parse a datagram; return None for keep-alives and garbage."""
    head, _, body = data.decode("utf-8", "replace").partition("\r\n\r\n")
    lines = head.split("\r\n")
    if len(lines) < 2 or not lines[0].strip():
        return None
    headers = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip().lower(), value.strip()))
    return SipMessage(lines[0].strip(), tuple(headers), body)


def caller_name(from_header: str) -> str:
    """Return the door panel name from a From header (its display name, once)."""
    found = re.match(r'\s*"?([^"<]*)"?\s*<', from_header)
    name = found[1].strip() if found else ""
    return name.split(" ")[0] if name else from_header


def digest_authorization(account: SipAccount, challenge: str, method: str, uri: str) -> str | None:
    """Answer a ``WWW-Authenticate: Digest`` challenge (MD5, no qop)."""
    params = {key: quoted or plain for key, quoted, plain in _PARAMS.findall(challenge)}
    if "nonce" not in params or params.get("qop"):
        return None
    realm = params.get("realm", account.realm)
    ha1 = hashlib.md5(f"{account.login}:{realm}:{account.password}".encode()).hexdigest()  # noqa: S324
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()  # noqa: S324
    response = hashlib.md5(f"{ha1}:{params['nonce']}:{ha2}".encode()).hexdigest()  # noqa: S324
    return (
        f'Digest username="{account.login}", realm="{realm}", nonce="{params["nonce"]}", '
        f'uri="{uri}", response="{response}", algorithm=MD5'
    )


@dataclass(frozen=True, slots=True)
class CallEvent:
    """A call started (``ring``) or ended (``end``)."""

    kind: str
    call_id: str
    caller: str


@dataclass(slots=True)
class Reaction:
    """What the agent wants after a message: datagrams to send and events to report."""

    send: list[bytes] = field(default_factory=list)
    events: list[CallEvent] = field(default_factory=list)
    registered: bool | None = None
    error: str = ""


class SipUser:
    """The protocol state of one registered device."""

    def __init__(self, account: SipAccount, local_host: str, local_port: int) -> None:
        """Create the agent that will be reachable at ``local_host:local_port``."""
        self._account = account
        self._contact = f"<sip:{account.login}@{local_host}:{local_port}>"
        self._via = f"SIP/2.0/UDP {local_host}:{local_port}"
        self._call_id = uuid.uuid4().hex
        self._tag = uuid.uuid4().hex[:8]
        self._cseq = 0
        self._challenged = False
        self._invites: dict[str, tuple[SipMessage, str]] = {}

    def register(self, authorization: str | None = None) -> bytes:
        """Build a REGISTER request."""
        if authorization is None:
            self._challenged = False
        self._cseq += 1
        account = self._account
        lines = [
            f"REGISTER sip:{account.realm} SIP/2.0",
            f"Via: {self._via};branch=z9hG4bK{uuid.uuid4().hex[:10]};rport",
            "Max-Forwards: 70",
            f"From: <sip:{account.login}@{account.realm}>;tag={self._tag}",
            f"To: <sip:{account.login}@{account.realm}>",
            f"Call-ID: {self._call_id}",
            f"CSeq: {self._cseq} REGISTER",
            f"Contact: {self._contact};expires={REGISTER_EXPIRES}",
            f"Expires: {REGISTER_EXPIRES}",
            "User-Agent: ha-moydom",
        ]
        if authorization:
            lines.append(f"Authorization: {authorization}")
        lines.append("Content-Length: 0")
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def receive(self, data: bytes) -> Reaction:
        """React to one datagram from the server."""
        message = parse(data)
        reaction = Reaction()
        if message is None:
            return reaction
        if message.method in _CALL_METHODS:
            _LOGGER.debug(
                "SIP %s call %s from %s, reason: %s",
                message.method,
                message.call_id,
                caller_name(message.header("from")),
                message.header("reason") or "-",
            )
        if message.status:
            self._on_response(message, reaction)
        elif message.method == "INVITE":
            self._on_invite(message, reaction)
        elif message.method == "CANCEL":
            self._on_cancel(message, reaction)
        elif message.method in _PEER_METHODS:
            reaction.send.append(self._reply(message, "200 OK", None))
            if message.method == "BYE":
                self._finish(message.call_id, reaction)
        return reaction

    def _on_response(self, message: SipMessage, reaction: Reaction) -> None:
        if "REGISTER" not in message.header("cseq"):
            return
        status = message.status
        if status == 200:
            self._challenged = False
            reaction.registered = True
        elif status in (401, 407) and not self._challenged:
            self._challenged = True
            challenge = message.header("www-authenticate") or message.header("proxy-authenticate")
            answer = digest_authorization(
                self._account, challenge, "REGISTER", f"sip:{self._account.realm}"
            )
            if answer is None:
                reaction.registered = False
                reaction.error = "unsupported authentication challenge"
            else:
                reaction.send.append(self.register(answer))
        elif status >= 300:
            reaction.registered = False
            reaction.error = message.start_line

    def _on_invite(self, message: SipMessage, reaction: Reaction) -> None:
        call_id = message.call_id
        known = self._invites.get(call_id)
        tag = known[1] if known else uuid.uuid4().hex[:8]
        reaction.send.append(self._reply(message, "100 Trying", tag))
        reaction.send.append(self._reply(message, "180 Ringing", tag))
        if known is None:
            self._invites[call_id] = (message, tag)
            reaction.events.append(CallEvent("ring", call_id, caller_name(message.header("from"))))

    def _on_cancel(self, message: SipMessage, reaction: Reaction) -> None:
        reaction.send.append(self._reply(message, "200 OK", None))
        known = self._invites.get(message.call_id)
        if known:
            reaction.send.append(self._reply(known[0], "487 Request Terminated", known[1]))
        self._finish(message.call_id, reaction)

    def _finish(self, call_id: str, reaction: Reaction) -> None:
        known = self._invites.pop(call_id, None)
        if known:
            reaction.events.append(CallEvent("end", call_id, caller_name(known[0].header("from"))))

    def forget(self, call_id: str) -> None:
        """Drop a call that timed out on our side."""
        self._invites.pop(call_id, None)

    def _reply(self, request: SipMessage, status: str, tag: str | None) -> bytes:
        to = request.header("to")
        if tag and "tag=" not in to:
            to += f";tag={tag}"
        lines = [f"SIP/2.0 {status}"]
        lines += [f"Via: {value}" for value in request.all("via")]
        lines += [f"Record-Route: {value}" for value in request.all("record-route")]
        lines += [
            f"From: {request.header('from')}",
            f"To: {to}",
            f"Call-ID: {request.call_id}",
            f"CSeq: {request.header('cseq')}",
            f"Contact: {self._contact}",
            "Content-Length: 0",
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode()


class SipClient(asyncio.DatagramProtocol):
    """Keep a device registered and report calls to ``on_event``."""

    def __init__(
        self,
        account: SipAccount,
        on_event: Callable[[CallEvent], None],
        on_registered: Callable[[bool, str], None] | None = None,
    ) -> None:
        """Create a client; call ``async_start`` to connect."""
        self._account = account
        self._on_event = on_event
        self._on_registered = on_registered
        self._user: SipUser | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task[None] | None = None
        self._registered: bool | None = None

    async def async_start(self, port: int = SIP_PORT) -> None:
        """Open the socket and start registering."""
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: self, remote_addr=(self._account.realm, port)
        )
        host, local_port = transport.get_extra_info("sockname")[:2]
        self._transport = transport  # type: ignore[assignment]
        self._user = SipUser(self._account, host, local_port)
        self._task = asyncio.create_task(self._keep_registered())

    async def _keep_registered(self) -> None:
        while True:
            self._send(self._user.register() if self._user else b"")
            await asyncio.sleep(REGISTER_EXPIRES / 2)

    def _send(self, data: bytes) -> None:
        if self._transport is not None and data:
            self._transport.sendto(data)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle a datagram from the server."""
        if self._user is None or len(data) > MAX_DATAGRAM_BYTES:
            return
        reaction = self._user.receive(data)
        for datagram in reaction.send:
            self._send(datagram)
        if reaction.registered is not None and reaction.registered != self._registered:
            self._registered = reaction.registered
            if self._on_registered is not None:
                self._on_registered(reaction.registered, reaction.error)
        for event in reaction.events:
            self._on_event(event)

    def forget(self, call_id: str) -> None:
        """Stop tracking a call that ended without a CANCEL or BYE."""
        if self._user is not None:
            self._user.forget(call_id)

    def error_received(self, exc: Exception) -> None:
        """Log socket errors (the next REGISTER retries)."""
        _LOGGER.debug("SIP socket error: %s", exc)

    def close(self) -> None:
        """Stop registering and close the socket."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
