"""Tests of the go2rtc client."""

from __future__ import annotations

import importlib

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

go2rtc = importlib.import_module("moydom_core.go2rtc")


@pytest.fixture
async def server():
    calls: list[tuple[str, str, dict[str, str]]] = []
    fail: set[str] = set()

    async def handler(request: web.Request) -> web.Response:
        path = request.path.rsplit("/", 1)[-1]
        calls.append((request.method, path, dict(request.query)))
        if path in fail:
            return web.Response(status=500, text="boom")
        return web.Response(text="")

    app = web.Application()
    app.router.add_route("*", "/api/{name}", handler)
    test_server = TestServer(app)
    await test_server.start_server()
    test_server.calls = calls  # type: ignore[attr-defined]
    test_server.fail = fail  # type: ignore[attr-defined]
    yield test_server
    await test_server.close()


async def test_publish_creates_the_stream_then_preloads_it(server) -> None:
    async with aiohttp.ClientSession() as session:
        client = go2rtc.Go2rtcClient(session, str(server.make_url("/")))
        await client.async_publish("moydom_1_light", "http://127.0.0.1:8123/relay?key=k&light=1")
    assert server.calls == [
        (
            "PUT",
            "streams",
            {"name": "moydom_1_light", "src": "http://127.0.0.1:8123/relay?key=k&light=1"},
        ),
        ("PUT", "preload", {"src": "moydom_1_light", "query": "video"}),
    ]


async def test_publish_reports_a_refusal(server) -> None:
    server.fail.add("preload")
    async with aiohttp.ClientSession() as session:
        client = go2rtc.Go2rtcClient(session, str(server.make_url("/")))
        with pytest.raises(go2rtc.Go2rtcError, match="HTTP 500"):
            await client.async_publish("n", "src")


async def test_unpublish_removes_preload_and_stream_and_ignores_errors(server) -> None:
    server.fail.add("preload")
    async with aiohttp.ClientSession() as session:
        client = go2rtc.Go2rtcClient(session, str(server.make_url("/")))
        await client.async_unpublish("n")
    assert [(m, p) for m, p, _ in server.calls] == [("DELETE", "preload"), ("DELETE", "streams")]


async def test_unreachable_server_raises() -> None:
    async with aiohttp.ClientSession() as session:
        client = go2rtc.Go2rtcClient(session, "http://127.0.0.1:1")
        with pytest.raises(go2rtc.Go2rtcError):
            await client.async_publish("n", "src")


@pytest.fixture
async def state_server():
    streams: dict[str, str] = {}
    preload: set[str] = set()
    calls: list[tuple[str, str]] = []

    async def stream_handler(request: web.Request) -> web.Response:
        calls.append((request.method, "streams"))
        name = request.query.get("name") or request.query.get("src", "")
        if request.method == "PUT":
            streams[name] = request.query["src"]
            return web.Response(text="")
        if name not in streams:
            return web.Response(status=404, text="not found")
        return web.json_response({"producers": [{"url": streams[name]}]})

    async def preload_handler(request: web.Request) -> web.Response:
        calls.append((request.method, "preload"))
        if request.method == "PUT":
            preload.add(request.query["src"])
            return web.Response(text="")
        return web.json_response({name: {} for name in preload})

    app = web.Application()
    app.router.add_route("*", "/api/streams", stream_handler)
    app.router.add_route("*", "/api/preload", preload_handler)
    test_server = TestServer(app)
    await test_server.start_server()
    test_server.calls = calls  # type: ignore[attr-defined]
    yield test_server
    await test_server.close()


async def test_ensure_publishes_once_and_leaves_a_matching_stream_alone(state_server) -> None:
    async with aiohttp.ClientSession() as session:
        client = go2rtc.Go2rtcClient(session, str(state_server.make_url("/")))
        await client.async_ensure("n", "http://relay/a")
        puts = [call for call in state_server.calls if call[0] == "PUT"]
        assert len(puts) == 2
        await client.async_ensure("n", "http://relay/a")
        assert [call for call in state_server.calls if call[0] == "PUT"] == puts
        await client.async_ensure("n", "http://relay/b")
        assert len([call for call in state_server.calls if call[0] == "PUT"]) == 4
