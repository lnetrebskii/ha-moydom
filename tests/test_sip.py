"""Tests of the SIP user agent protocol logic."""

from __future__ import annotations

import asyncio
import hashlib
import importlib

import pytest

sip = importlib.import_module("moydom_core.sip")
SipAccount, SipUser = sip.SipAccount, sip.SipUser
caller_name, digest_authorization, parse = sip.caller_name, sip.digest_authorization, sip.parse

ACCOUNT = SipAccount("dev1", "secret", "41678.example.ru")
INVITE = (
    "INVITE sip:dev1@41678.example.ru SIP/2.0\r\n"
    "Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bKaaa\r\n"
    "Via: SIP/2.0/UDP 10.0.0.2:11000;branch=z9hG4bKbbb\r\n"
    "Record-Route: <sip:87.0.0.1:5060;lr=on>\r\n"
    'From: "255e826a0d0a 255e826a0d0a" <sip:000@41678.example.ru>;tag=abc\r\n'
    "To: <sip:dev1@41678.example.ru>\r\n"
    "Call-ID: call-1\r\n"
    "CSeq: 7 INVITE\r\n"
    "Content-Length: 0\r\n\r\n"
)


def request(method: str, call_id: str = "call-1") -> bytes:
    return (
        f"{method} sip:dev1@41678.example.ru SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bKaaa\r\n"
        'From: "255e826a0d0a" <sip:000@41678.example.ru>;tag=abc\r\n'
        "To: <sip:dev1@41678.example.ru>\r\n"
        f"Call-ID: {call_id}\r\nCSeq: 7 {method}\r\nContent-Length: 0\r\n\r\n"
    ).encode()


def user() -> SipUser:
    return SipUser(ACCOUNT, "192.168.1.5", 5555)


def test_parse_request_and_response() -> None:
    message = parse(INVITE.encode())
    assert message is not None
    assert message.method == "INVITE"
    assert message.status == 0
    assert message.call_id == "call-1"
    assert len(list(message.all("via"))) == 2
    response = parse(b"SIP/2.0 401 Unauthorized\r\nCSeq: 1 REGISTER\r\n\r\n")
    assert response is not None
    assert response.status == 401
    assert response.method == ""


def test_parse_ignores_keepalive() -> None:
    assert parse(b"\r\n\r\n") is None
    assert parse(b"") is None


def test_caller_name_is_the_panel_id_once() -> None:
    assert caller_name('"255e826a0d0a 255e826a0d0a" <sip:000@x>;tag=1') == "255e826a0d0a"
    assert caller_name("<sip:000@x>") == "<sip:000@x>"


def test_digest_matches_rfc2617() -> None:
    challenge = 'Digest realm="41678.example.ru", nonce="n0nce"'
    header = digest_authorization(ACCOUNT, challenge, "REGISTER", "sip:41678.example.ru")
    ha1 = hashlib.md5(b"dev1:41678.example.ru:secret").hexdigest()
    ha2 = hashlib.md5(b"REGISTER:sip:41678.example.ru").hexdigest()
    expected = hashlib.md5(f"{ha1}:n0nce:{ha2}".encode()).hexdigest()
    assert header is not None
    assert f'response="{expected}"' in header


def test_digest_refuses_qop() -> None:
    assert (
        digest_authorization(ACCOUNT, 'Digest nonce="a", qop="auth"', "REGISTER", "sip:x") is None
    )


def test_register_answers_the_challenge_once() -> None:
    agent = user()
    first = agent.register().decode()
    assert "REGISTER sip:41678.example.ru" in first
    assert "Authorization" not in first
    challenge = b'SIP/2.0 401 Unauthorized\r\nCSeq: 1 REGISTER\r\nWWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n'
    reaction = agent.receive(challenge)
    assert len(reaction.send) == 1
    assert b'Authorization: Digest username="dev1"' in reaction.send[0]
    assert agent.receive(challenge).registered is False


def test_next_register_answers_a_new_challenge_after_a_failed_round() -> None:
    agent = user()
    challenge = b'SIP/2.0 401 Unauthorized\r\nCSeq: 1 REGISTER\r\nWWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n'
    agent.register()
    agent.receive(challenge)
    assert agent.receive(challenge).registered is False
    agent.register()
    again = agent.receive(challenge)
    assert again.registered is None
    assert b"Authorization: Digest" in again.send[0]


def test_call_signalling_is_logged_with_its_reason(caplog: pytest.LogCaptureFixture) -> None:
    agent = user()
    agent.receive(INVITE.encode())
    cancel = request("CANCEL").replace(
        b"Content-Length",
        b'Reason: SIP;cause=200;text="Call completed elsewhere"\r\nContent-Length',
    )
    with caplog.at_level("DEBUG", logger=sip.__name__):
        agent.receive(cancel)
    assert "SIP CANCEL call call-1 from 255e826a0d0a, reason: SIP;cause=200" in caplog.text


def test_register_ok_and_failure() -> None:
    agent = user()
    assert agent.receive(b"SIP/2.0 200 OK\r\nCSeq: 2 REGISTER\r\n\r\n").registered is True
    failed = agent.receive(b"SIP/2.0 403 Forbidden\r\nCSeq: 3 REGISTER\r\n\r\n")
    assert failed.registered is False
    assert "403" in failed.error


def test_invite_rings_once_and_never_picks_up() -> None:
    agent = user()
    reaction = agent.receive(INVITE.encode())
    assert [e.kind for e in reaction.events] == ["ring"]
    assert reaction.events[0].caller == "255e826a0d0a"
    statuses = [r.split(b"\r\n", 1)[0] for r in reaction.send]
    assert statuses == [b"SIP/2.0 100 Trying", b"SIP/2.0 180 Ringing"]
    assert all(b"branch=z9hG4bKaaa" in r and b"branch=z9hG4bKbbb" in r for r in reaction.send)
    assert b"Record-Route: <sip:87.0.0.1:5060;lr=on>" in reaction.send[1]
    tag = reaction.send[1].split(b"tag=")[-1].split(b"\r\n")[0]
    again = agent.receive(INVITE.encode())
    assert again.events == []
    assert tag in again.send[1]


def test_cancel_ends_the_call() -> None:
    agent = user()
    agent.receive(INVITE.encode())
    reaction = agent.receive(request("CANCEL"))
    assert [e.kind for e in reaction.events] == ["end"]
    statuses = [r.split(b"\r\n", 1)[0] for r in reaction.send]
    assert statuses == [b"SIP/2.0 200 OK", b"SIP/2.0 487 Request Terminated"]
    assert agent.receive(request("CANCEL")).events == []


def test_bye_ends_the_call_and_peers_get_200() -> None:
    agent = user()
    agent.receive(INVITE.encode())
    assert [e.kind for e in agent.receive(request("BYE")).events] == ["end"]
    notify = agent.receive(request("NOTIFY", "other"))
    assert notify.events == []
    assert notify.send[0].startswith(b"SIP/2.0 200 OK")


def test_forget_allows_the_same_call_id_again() -> None:
    agent = user()
    agent.receive(INVITE.encode())
    agent.forget("call-1")
    assert [e.kind for e in agent.receive(INVITE.encode()).events] == ["ring"]


def test_client_registers_and_reports_a_call_over_udp() -> None:
    account = SipAccount("dev1", "secret", "127.0.0.1")
    rings: list[object] = []
    seen: list[bytes] = []

    class Server(asyncio.DatagramProtocol):
        def connection_made(self, transport: asyncio.DatagramTransport) -> None:
            self.transport = transport

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            seen.append(data)
            if data.startswith(b"REGISTER") and b"Authorization" not in data:
                self.transport.sendto(
                    b"SIP/2.0 401 Unauthorized\r\nCSeq: 1 REGISTER\r\n"
                    b'WWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n',
                    addr,
                )
            elif data.startswith(b"REGISTER"):
                self.transport.sendto(b"SIP/2.0 200 OK\r\nCSeq: 2 REGISTER\r\n\r\n", addr)
                self.transport.sendto(INVITE.encode(), addr)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(Server, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        client = sip.SipClient(account, rings.append)
        await client.async_start(port)
        for _ in range(100):
            if rings and any(d.startswith(b"SIP/2.0 180") for d in seen):
                break
            await asyncio.sleep(0.02)
        client.close()
        transport.close()

    asyncio.run(scenario())
    assert [e.kind for e in rings] == ["ring"]
    assert any(d.startswith(b"SIP/2.0 180 Ringing") for d in seen)
