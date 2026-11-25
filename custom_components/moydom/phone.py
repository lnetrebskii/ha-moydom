"""Phone number helpers."""

from __future__ import annotations

PHONE_LENGTH = 11


def normalize_phone(raw: str) -> str | None:
    """Return the phone as ``7XXXXXXXXXX`` or None when it is not a Russian number."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == PHONE_LENGTH and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == PHONE_LENGTH and digits[0] == "7":
        return digits
    return None
