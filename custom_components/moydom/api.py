"""Async client for the Мой Дом (myhome.proptech.ru) mobile API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

import aiohttp

from .const import (
    ANDROID_VERSION,
    APP_BRAND,
    APP_VERSION,
    APP_VERSION_CODE,
    BASE_URL,
    DEVICE_NAME,
    LOGIN_USER_AGENT,
    OPEN_ACTION_NAME,
    REQUEST_TIMEOUT_SECONDS,
)
from .models import AccessControl, Account, Place, Tokens

_LOGGER = logging.getLogger(__name__)

TokensCallback = Callable[[Tokens], Awaitable[None]]


class MoydomError(Exception):
    """Base error of the integration."""


class ApiError(MoydomError):
    """The server answered with an error or could not be reached."""


class AuthError(MoydomError):
    """The session is invalid and a new SMS login is required."""


class InvalidCodeError(MoydomError):
    """The SMS code was rejected."""


class MoydomApi:
    """Client holding one login session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        installation_id: str,
        tokens: Tokens | None = None,
        *,
        account_id: str = "",
        place_id: int = 0,
        on_tokens: TokensCallback | None = None,
    ) -> None:
        """Create a client; ``tokens`` may be omitted before the SMS login."""
        self._session = session
        self._installation_id = installation_id
        self._tokens = tokens
        self._account_id = account_id
        self._place_id = place_id
        self._on_tokens = on_tokens

    @property
    def tokens(self) -> Tokens | None:
        """Return the current tokens."""
        return self._tokens

    def _user_agent(self) -> str:
        """Build the app-style User-Agent that the forpost API requires."""
        if self._tokens is None or not self._account_id:
            return LOGIN_USER_AGENT
        return " | ".join(
            (
                DEVICE_NAME,
                ANDROID_VERSION,
                APP_BRAND,
                f"{APP_VERSION} ({APP_VERSION_CODE})",
                self._account_id,
                str(self._tokens.operator_id),
                self._installation_id,
                str(self._place_id),
            )
        )

    def _headers(self, *, auth: bool) -> dict[str, str]:
        headers = {"User-Agent": self._user_agent(), "App-Version": APP_VERSION}
        if auth and self._tokens is not None:
            headers["Authorization"] = f"Bearer {self._tokens.access}"
            headers["Operator"] = str(self._tokens.operator_id)
        return headers

    async def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, bytes]:
        """Send one request and return status and body."""
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}/{path}",
                headers=headers,
                json=json_body,
                params=params,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return response.status, await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"{method} {path}: {err}"
            raise ApiError(msg) from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> tuple[int, bytes]:
        """Send an authorised request, refreshing the access token once on 401."""
        status, body = await self._send(
            method, path, headers=self._headers(auth=auth), json_body=json_body, params=params
        )
        if status == 401 and auth and retry:
            await self.async_refresh()
            return await self._request(
                method, path, auth=auth, json_body=json_body, params=params, retry=False
            )
        if status == 401 and auth:
            msg = "Access token rejected after refresh"
            raise AuthError(msg)
        return status, body

    @staticmethod
    def _parse(status: int, body: bytes, path: str) -> Any:
        """Decode a JSON body of a successful response."""
        if not 200 <= status < 300:
            msg = f"{path}: HTTP {status}"
            raise ApiError(msg)
        try:
            return json.loads(body)
        except ValueError as err:
            msg = f"{path}: invalid JSON"
            raise ApiError(msg) from err

    async def async_refresh(self) -> Tokens:
        """Get a new access token using the refresh token (header ``Bearer``)."""
        if self._tokens is None:
            msg = "No session to refresh"
            raise AuthError(msg)
        headers = self._headers(auth=False)
        headers["Bearer"] = self._tokens.refresh
        headers["Operator"] = str(self._tokens.operator_id)
        status, body = await self._send("GET", "auth/v2/session/refresh", headers=headers)
        if status != 200:
            msg = f"Session refresh failed: HTTP {status}"
            raise AuthError(msg)
        data = self._parse(status, body, "auth/v2/session/refresh")
        await self._store_tokens(
            Tokens(data["accessToken"], data["refreshToken"], int(data["operatorId"]))
        )
        return self._tokens  # type: ignore[return-value]

    async def _store_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        if self._on_tokens is not None:
            await self._on_tokens(tokens)

    async def async_login(self, phone: str) -> list[Account]:
        """Return the contracts bound to the phone number."""
        path = f"auth/v2/login/{phone}"
        status, body = await self._request("GET", path, auth=False)
        if status not in (200, 300):
            msg = f"{path}: HTTP {status}"
            raise ApiError(msg)
        data = json.loads(body) if body else []
        if not isinstance(data, list) or not data:
            msg = "No contracts are bound to this phone number"
            raise ApiError(msg)
        return [Account.from_json(item) for item in data]

    async def async_send_code(self, phone: str, account: Account) -> None:
        """Ask the server to send an SMS code for the chosen contract."""
        path = f"auth/v2/confirmation/{phone}"
        status, _ = await self._request("POST", path, auth=False, json_body=account.to_json())
        if status != 200:
            msg = f"{path}: HTTP {status}"
            raise ApiError(msg)

    async def async_confirm(self, phone: str, account: Account, code: str) -> Tokens:
        """Exchange the SMS code for tokens."""
        path = f"auth/v3/auth/{phone}/confirmation"
        body_json = {
            "operatorId": account.operator_id,
            "login": phone,
            "accountId": account.account_id,
            "profileId": account.profile_id,
            "confirm1": code,
            "confirm2": code,
            "subscriberId": account.subscriber_id,
        }
        status, body = await self._request("POST", path, auth=False, json_body=body_json)
        if 400 <= status < 500:
            raise InvalidCodeError(path)
        data = self._parse(status, body, path)
        self._account_id = account.account_id
        self._place_id = account.place_id
        tokens = Tokens(data["accessToken"], data["refreshToken"], int(data["operatorId"]))
        await self._store_tokens(tokens)
        return tokens

    async def async_get_places(self) -> list[Place]:
        """Return the addresses of the subscriber."""
        path = "rest/v3/subscriber-places"
        status, body = await self._request("GET", path)
        return [Place.from_json(item) for item in self._parse(status, body, path)["data"]]

    async def async_get_access_controls(self, place_id: int) -> list[AccessControl]:
        """Return the intercoms of an address."""
        path = f"rest/v1/places/{place_id}/accesscontrols"
        status, body = await self._request("GET", path)
        items = self._parse(status, body, path)["data"]
        return [AccessControl.from_json(place_id, item) for item in items]

    async def async_snapshot(self, control: AccessControl, width: int, height: int) -> bytes:
        """Return a JPEG frame of the intercom camera."""
        path = f"rest/v1/places/{control.place_id}/accesscontrols/{control.control_id}/snapshots"
        status, body = await self._request("GET", path, params={"width": width, "height": height})
        if status != 200 or not body:
            msg = f"{path}: HTTP {status}"
            raise ApiError(msg)
        return body

    async def async_video_url(self, camera_id: int, *, light: bool = False) -> str:
        """Return a fresh live stream URL (H.264 over HTTPS).

        The light stream is 960x528 instead of 1920x1080: indoor monitors decode it.
        """
        session_path = "api/mh-camera-personal/mobile/v1/video/refresh-user-session"
        status, _ = await self._request("PUT", session_path, params={"externalCameraId": camera_id})
        if status != 200:
            _LOGGER.debug("Video session refresh returned HTTP %s", status)
        path = f"rest/v1/forpost/cameras/{camera_id}/video"
        status, body = await self._request(
            "GET", path, params={"LightStream": int(light), "Format": "H264"}
        )
        data = self._parse(status, body, path)["data"]
        if not data.get("URL"):
            msg = f"{path}: {data.get('Error') or 'no stream URL'}"
            raise ApiError(msg)
        return str(data["URL"])

    async def async_open_door(self, control: AccessControl, entrance_id: int | None = None) -> None:
        """Open the door of the intercom."""
        path = f"rest/v1/places/{control.place_id}/accesscontrols/{control.control_id}"
        if entrance_id is not None:
            path += f"/entrances/{entrance_id}"
        status, body = await self._request(
            "POST", f"{path}/actions", json_body={"name": OPEN_ACTION_NAME}
        )
        result = self._parse(status, body, path)["data"]
        if not result.get("status"):
            msg = f"{path}: {result.get('errorMessage') or 'door was not opened'}"
            raise ApiError(msg)
