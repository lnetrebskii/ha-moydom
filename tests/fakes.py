"""A scripted stand-in for ``aiohttp.ClientSession``."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass
class Call:
    """One recorded request."""

    method: str
    url: str
    headers: dict[str, str]
    json: Any
    params: dict[str, Any] | None


class FakeResponse:
    """Minimal response object."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class FakeSession:
    """Return queued responses in order and remember every call."""

    responses: list[tuple[int, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def push(self, status: int, payload: Any = None) -> None:
        self.responses.append((status, payload))

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(
            Call(method, url, kwargs["headers"], kwargs.get("json"), kwargs.get("params"))
        )
        status, payload = self.responses.pop(0)
        body = b"" if payload is None else json.dumps(payload).encode()
        return FakeResponse(status, body)
