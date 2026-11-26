"""Tests of the Мой Дом API client."""

from __future__ import annotations

import importlib

import pytest

from tests.fakes import FakeSession

api_mod = importlib.import_module("moydom_core.api")
models = importlib.import_module("moydom_core.models")
phone_mod = importlib.import_module("moydom_core.phone")

TOKENS = models.Tokens("access-1", "refresh-1", 32)
LOGIN_ITEM = {
    "operatorId": 32,
    "subscriberId": 7770001,
    "accountId": "100200300400",
    "placeId": 5550001,
    "address": "Ле***на, дом. ***",
    "profileId": None,
}
INTERCOM = {
    "id": 9001,
    "name": "Ленина 1  (п. 1)",
    "allowOpen": True,
    "allowVideo": True,
    "externalCameraId": "8880001",
    "entrances": [],
}


def make_api(session: FakeSession, saved: list | None = None):
    async def on_tokens(tokens) -> None:
        if saved is not None:
            saved.append(tokens)

    return api_mod.MoydomApi(
        session,
        "iid-1",
        TOKENS,
        account_id="100200300400",
        place_id=5550001,
        on_tokens=on_tokens,
    )


def test_user_agent_matches_the_app_format() -> None:
    api = make_api(FakeSession())
    parts = ("Google Pixel 7", "Android 14", "ntk", "9.12.0 (91200000)")
    ids = ("100200300400", "32", "iid-1", "5550001")
    expected = " | ".join((*parts, *ids))
    assert api._user_agent() == expected


def test_user_agent_before_login_is_plain() -> None:
    api = api_mod.MoydomApi(FakeSession(), "iid-1")
    assert api._user_agent() == "Android"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("79001234567", "79001234567"),
        ("+7 (900) 123-45-67", "79001234567"),
        ("89001234567", "79001234567"),
        ("900123456", None),
        ("19001234562", None),
    ],
)
def test_normalize_phone(raw: str, expected: str | None) -> None:
    assert phone_mod.normalize_phone(raw) == expected


async def test_login_returns_accounts_from_http_300() -> None:
    session = FakeSession()
    session.push(300, [LOGIN_ITEM])
    accounts = await api_mod.MoydomApi(session, "iid-1").async_login("79001234567")
    assert accounts[0].account_id == "100200300400"
    assert session.calls[0].url.endswith("/auth/v2/login/79001234567")
    assert "Authorization" not in session.calls[0].headers


async def test_login_without_contracts_fails() -> None:
    session = FakeSession()
    session.push(200, [])
    with pytest.raises(api_mod.ApiError):
        await api_mod.MoydomApi(session, "iid-1").async_login("79001234567")


async def test_confirm_sends_code_twice_and_stores_tokens() -> None:
    session = FakeSession()
    session.push(200, {"accessToken": "a", "refreshToken": "r", "operatorId": 32})
    saved: list = []
    api = api_mod.MoydomApi(session, "iid-1", on_tokens=lambda t: _append(saved, t))
    account = models.Account.from_json(LOGIN_ITEM)
    tokens = await api.async_confirm("79001234567", account, "1234")
    body = session.calls[0].json
    assert body["confirm1"] == body["confirm2"] == "1234"
    assert body["subscriberId"] == 7770001
    assert tokens == models.Tokens("a", "r", 32)
    assert saved == [tokens]
    assert "100200300400" in api._user_agent()


async def _append(target: list, value: object) -> None:
    target.append(value)


async def test_wrong_code_raises_invalid_code() -> None:
    session = FakeSession()
    session.push(400, {"errorCode": "3001"})
    api = api_mod.MoydomApi(session, "iid-1")
    with pytest.raises(api_mod.InvalidCodeError):
        await api.async_confirm("79001234567", models.Account.from_json(LOGIN_ITEM), "0000")


async def test_expired_access_token_is_refreshed_and_request_retried() -> None:
    session = FakeSession()
    session.push(401)
    session.push(200, {"accessToken": "access-2", "refreshToken": "refresh-1", "operatorId": 32})
    session.push(200, {"data": []})
    saved: list = []
    places = await make_api(session, saved).async_get_places()
    assert places == []
    refresh = session.calls[1]
    assert refresh.url.endswith("/auth/v2/session/refresh")
    assert refresh.headers["Bearer"] == "refresh-1"
    assert "Authorization" not in refresh.headers
    assert session.calls[2].headers["Authorization"] == "Bearer access-2"
    assert saved[-1].access == "access-2"


async def test_failed_refresh_asks_for_a_new_login() -> None:
    session = FakeSession()
    session.push(401)
    session.push(401)
    with pytest.raises(api_mod.AuthError):
        await make_api(session).async_get_places()


async def test_access_controls_are_parsed() -> None:
    session = FakeSession()
    session.push(200, {"data": [INTERCOM]})
    controls = await make_api(session).async_get_access_controls(5550001)
    assert controls[0].camera_id == 8880001
    assert controls[0].name == "Ленина 1  (п. 1)"
    assert controls[0].allow_open


async def test_video_url_refreshes_the_session_first() -> None:
    session = FakeSession()
    session.push(200, {"data": {"status": True}})
    session.push(200, {"data": {"URL": "https://cctv.example:18080/rtsp/a/b/d=1", "Error": None}})
    url = await make_api(session).async_video_url(8880001)
    assert url == "https://cctv.example:18080/rtsp/a/b/d=1"
    assert session.calls[0].method == "PUT"
    assert session.calls[0].params == {"externalCameraId": 8880001}
    assert session.calls[1].params == {"LightStream": 0, "Format": "H264"}
    assert session.calls[1].headers["User-Agent"].startswith("Google Pixel 7")


async def test_light_video_url_asks_for_the_light_stream() -> None:
    session = FakeSession()
    session.push(200, {"data": {"status": True}})
    session.push(200, {"data": {"URL": "https://cctv.example:18080/rtsp/a/b/d=2", "Error": None}})
    await make_api(session).async_video_url(8880001, light=True)
    assert session.calls[1].params == {"LightStream": 1, "Format": "H264"}


async def test_video_url_missing_raises() -> None:
    session = FakeSession()
    session.push(200, {"data": {}})
    session.push(200, {"data": {"URL": None, "Error": "no access"}})
    with pytest.raises(api_mod.ApiError):
        await make_api(session).async_video_url(1)


async def test_open_door_posts_the_action() -> None:
    session = FakeSession()
    session.push(200, {"data": {"status": True, "errorCode": None, "errorMessage": None}})
    control = models.AccessControl.from_json(5550001, INTERCOM)
    await make_api(session).async_open_door(control)
    call = session.calls[0]
    assert call.url.endswith("/rest/v1/places/5550001/accesscontrols/9001/actions")
    assert call.json == {"name": "accessControlOpen"}


async def test_open_entrance_uses_the_entrance_path() -> None:
    session = FakeSession()
    session.push(200, {"data": {"status": True}})
    control = models.AccessControl.from_json(5550001, INTERCOM)
    await make_api(session).async_open_door(control, 5)
    assert session.calls[0].url.endswith("/accesscontrols/9001/entrances/5/actions")


async def test_open_door_refused_raises() -> None:
    session = FakeSession()
    session.push(200, {"data": {"status": False, "errorMessage": "busy"}})
    control = models.AccessControl.from_json(5550001, INTERCOM)
    with pytest.raises(api_mod.ApiError, match="busy"):
        await make_api(session).async_open_door(control)


async def test_snapshot_with_empty_body_raises() -> None:
    session = FakeSession()
    session.responses.append((200, None))
    control = models.AccessControl.from_json(5550001, INTERCOM)
    with pytest.raises(api_mod.ApiError):
        await make_api(session).async_snapshot(control, 640, 360)
    assert session.calls[0].params == {"width": 640, "height": 360}
