"""Data models returned by the Мой Дом API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Tokens:
    """Access and refresh tokens of one session."""

    access: str
    refresh: str
    operator_id: int


@dataclass(frozen=True, slots=True)
class Account:
    """One contract (address) bound to a phone number."""

    operator_id: int
    subscriber_id: int
    account_id: str
    place_id: int
    address: str | None
    profile_id: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Account:
        """Build an account from an ``auth/v2/login`` list item."""
        return cls(
            operator_id=int(data["operatorId"]),
            subscriber_id=int(data["subscriberId"]),
            account_id=str(data["accountId"]),
            place_id=int(data["placeId"]),
            address=data.get("address"),
            profile_id=data.get("profileId"),
        )

    def to_json(self) -> dict[str, Any]:
        """Return the body for ``auth/v2/confirmation``."""
        return {
            "operatorId": self.operator_id,
            "accountId": self.account_id,
            "address": self.address,
            "profileId": self.profile_id,
            "subscriberId": self.subscriber_id,
            "placeId": self.place_id,
        }


@dataclass(frozen=True, slots=True)
class Place:
    """An address the subscriber has access to."""

    place_id: int
    address: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Place:
        """Build a place from a ``subscriber-places`` item."""
        place = data["place"]
        address = place.get("address") or {}
        return cls(
            place_id=int(place["id"]),
            address=address.get("visibleAddress") or str(place["id"]),
        )


@dataclass(frozen=True, slots=True)
class AccessControl:
    """An intercom (door panel) with optional video and door opener."""

    place_id: int
    control_id: int
    name: str
    camera_id: int | None
    allow_open: bool
    allow_video: bool
    entrance_ids: tuple[int, ...]

    @classmethod
    def from_json(cls, place_id: int, data: dict[str, Any]) -> AccessControl:
        """Build an access control from an ``accesscontrols`` item."""
        camera = data.get("externalCameraId")
        return cls(
            place_id=place_id,
            control_id=int(data["id"]),
            name=str(data.get("name") or data["id"]).strip(),
            camera_id=int(camera) if camera else None,
            allow_open=bool(data.get("allowOpen")),
            allow_video=bool(data.get("allowVideo")),
            entrance_ids=tuple(
                int(entrance["id"]) for entrance in data.get("entrances") or [] if "id" in entrance
            ),
        )
