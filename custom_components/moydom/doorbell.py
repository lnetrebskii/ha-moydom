"""Doorbell state of the intercoms, fed by SIP calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import MoydomError
from .const import RING_TIMEOUT_SECONDS, SIP_RETRY_SECONDS
from .sip import CallEvent, SipClient

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .coordinator import MoydomCoordinator
    from .models import AccessControl

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Ring:
    """The call that is ringing now or rang last."""

    call_id: str
    caller: str
    started: datetime


class Doorbell:
    """Whether somebody is calling from an intercom, and who."""

    def __init__(self) -> None:
        """Create an idle doorbell."""
        self.ring: Ring | None = None
        self.last: Ring | None = None
        self._listeners: list[Callable[[CallEvent], None]] = []

    @property
    def ringing(self) -> bool:
        """Return True while a call is ringing."""
        return self.ring is not None

    def add_listener(self, listener: Callable[[CallEvent], None]) -> Callable[[], None]:
        """Call ``listener`` on every ring and end; return the remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def notify(self, event: CallEvent) -> None:
        """Apply a call event and tell the entities."""
        if event.kind == "ring":
            self.ring = self.last = Ring(event.call_id, event.caller, dt_util.utcnow())
        elif self.ring is not None and self.ring.call_id == event.call_id:
            self.ring = None
        else:
            return
        for listener in list(self._listeners):
            listener(event)


class CallListener:
    """Register every intercom of the account as a SIP device and follow its calls."""

    def __init__(self, hass: HomeAssistant, coordinator: MoydomCoordinator) -> None:
        """Create the listener; ``async_sync`` starts it."""
        self._hass = hass
        self._coordinator = coordinator
        self._clients: dict[int, SipClient] = {}
        self._timers: dict[str, Callable[[], None]] = {}
        self._retry: Callable[[], None] | None = None

    async def async_sync(self) -> None:
        """Start a SIP client for every intercom that has none yet."""
        failed = False
        for control in self._coordinator.data.values():
            if control.control_id in self._clients:
                continue
            failed |= not await self._async_start(control)
        if failed and self._retry is None:
            self._retry = async_call_later(self._hass, SIP_RETRY_SECONDS, self._retry_sync)

    def _retry_sync(self, _now: datetime) -> None:
        self._retry = None
        self._hass.async_create_background_task(self.async_sync(), "moydom_sip_retry")

    async def _async_start(self, control: AccessControl) -> bool:
        control_id = control.control_id
        try:
            account = await self._coordinator.api.async_sip_account(control)
            client = SipClient(
                account,
                lambda event: self._on_event(control_id, client, event),
                lambda ok, error: self._on_registered(control, ok, error),
            )
            await client.async_start()
        except (MoydomError, OSError) as err:
            _LOGGER.warning("Cannot listen to calls of %s: %s", control.name, err)
            return False
        self._clients[control_id] = client
        return True

    def _on_registered(self, control: AccessControl, ok: bool, error: str) -> None:
        if ok:
            _LOGGER.debug("%s: registered to receive calls", control.name)
        else:
            _LOGGER.warning("Calls of %s are not received: %s", control.name, error)

    def _on_event(self, control_id: int, client: SipClient, event: CallEvent) -> None:
        _LOGGER.debug("Intercom %s: %s of call %s", control_id, event.kind, event.call_id)
        doorbell = self._coordinator.doorbell(control_id)
        key = f"{control_id}:{event.call_id}"
        if event.kind == "ring":
            self._cancel_timer(key)
            self._timers[key] = async_call_later(
                self._hass,
                RING_TIMEOUT_SECONDS,
                lambda _now: self._expire(control_id, client, event),
            )
        else:
            self._cancel_timer(key)
        doorbell.notify(event)

    def _expire(self, control_id: int, client: SipClient, event: CallEvent) -> None:
        _LOGGER.debug(
            "Call %s of intercom %s got no CANCEL in %s s",
            event.call_id,
            control_id,
            RING_TIMEOUT_SECONDS,
        )
        self._timers.pop(f"{control_id}:{event.call_id}", None)
        client.forget(event.call_id)
        self._coordinator.doorbell(control_id).notify(CallEvent("end", event.call_id, event.caller))

    def _cancel_timer(self, key: str) -> None:
        cancel = self._timers.pop(key, None)
        if cancel is not None:
            cancel()

    def close(self) -> None:
        """Stop every client and timer."""
        if self._retry is not None:
            self._retry()
            self._retry = None
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()
        for client in self._clients.values():
            client.close()
        self._clients.clear()
