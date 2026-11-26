"""Config flow: phone number, contract choice, SMS code."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, InvalidCodeError, MoydomApi
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_GO2RTC_URL,
    CONF_INSTALLATION_ID,
    CONF_ONVIF,
    CONF_OPERATOR_ID,
    CONF_PHONE,
    CONF_PLACE_ID,
    CONF_REFRESH_TOKEN,
    CONF_RTSP_PORT,
    DEFAULT_GO2RTC_URL,
    DEFAULT_ONVIF,
    DEFAULT_RTSP_PORT,
    DOMAIN,
)
from .models import Account
from .phone import normalize_phone

_LOGGER = logging.getLogger(__name__)


class MoydomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the SMS login."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MoydomOptionsFlow:
        """Return the options flow."""
        return MoydomOptionsFlow()

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._api: MoydomApi | None = None
        self._installation_id = str(uuid.uuid4())
        self._phone = ""
        self._accounts: list[Account] = []
        self._account: Account | None = None
        self._reauth = False

    def _client(self) -> MoydomApi:
        if self._api is None:
            self._api = MoydomApi(async_get_clientsession(self.hass), self._installation_id)
        return self._api

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the phone number registered in the app."""
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = normalize_phone(user_input[CONF_PHONE])
            if phone is None:
                errors[CONF_PHONE] = "invalid_phone"
            else:
                self._phone = phone
                try:
                    self._accounts = await self._client().async_login(phone)
                except ApiError:
                    _LOGGER.exception("Login lookup failed")
                    errors["base"] = "cannot_connect"
                else:
                    return await self._after_accounts()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_PHONE): str}),
            errors=errors,
        )

    async def _after_accounts(self) -> ConfigFlowResult:
        """Pick the contract automatically when there is only one."""
        if self._reauth:
            stored = self._get_reauth_entry().data[CONF_ACCOUNT_ID]
            self._accounts = [a for a in self._accounts if a.account_id == stored] or self._accounts
        if len(self._accounts) == 1:
            self._account = self._accounts[0]
            return await self._send_code()
        return await self.async_step_account()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the contract when the phone has several."""
        if user_input is not None:
            self._account = next(a for a in self._accounts if a.account_id == user_input["account"])
            return await self._send_code()
        options = {
            a.account_id: f"{a.address or a.account_id} (…{a.account_id[-4:]})"
            for a in self._accounts
        }
        return self.async_show_form(
            step_id="account", data_schema=vol.Schema({vol.Required("account"): vol.In(options)})
        )

    async def _send_code(self) -> ConfigFlowResult:
        """Request the SMS and move to the code step."""
        assert self._account is not None  # noqa: S101
        try:
            await self._client().async_send_code(self._phone, self._account)
        except ApiError:
            _LOGGER.exception("SMS request failed")
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_code()

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the SMS code and finish the login."""
        errors: dict[str, str] = {}
        if user_input is not None and self._account is not None:
            try:
                tokens = await self._client().async_confirm(
                    self._phone, self._account, user_input["code"].strip()
                )
            except InvalidCodeError:
                errors["code"] = "invalid_code"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish(tokens.access, tokens.refresh, tokens.operator_id)
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={"phone": f"+{self._phone}"},
            errors=errors,
        )

    async def _finish(self, access: str, refresh: str, operator_id: int) -> ConfigFlowResult:
        """Create or update the config entry."""
        assert self._account is not None  # noqa: S101
        data = {
            CONF_PHONE: self._phone,
            CONF_INSTALLATION_ID: self._installation_id,
            CONF_ACCESS_TOKEN: access,
            CONF_REFRESH_TOKEN: refresh,
            CONF_OPERATOR_ID: operator_id,
            CONF_ACCOUNT_ID: self._account.account_id,
            CONF_PLACE_ID: self._account.place_id,
        }
        await self.async_set_unique_id(f"{operator_id}_{self._account.account_id}")
        if self._reauth:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        self._abort_if_unique_id_configured()
        title = self._account.address or self._account.account_id
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start a new SMS login when the stored session expired."""
        self._reauth = True
        self._phone = entry_data[CONF_PHONE]
        self._installation_id = entry_data[CONF_INSTALLATION_ID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for confirmation, then send the SMS."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"phone": f"+{self._phone}"},
            )
        try:
            self._accounts = await self._client().async_login(self._phone)
        except ApiError:
            return self.async_abort(reason="cannot_connect")
        return await self._after_accounts()


class MoydomOptionsFlow(OptionsFlow):
    """Options of the indoor monitor (ONVIF camera) support."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit whether intercoms are shown to monitors and where go2rtc is."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_ONVIF, default=options.get(CONF_ONVIF, DEFAULT_ONVIF)): bool,
                vol.Required(
                    CONF_GO2RTC_URL, default=options.get(CONF_GO2RTC_URL, DEFAULT_GO2RTC_URL)
                ): cv.url,
                vol.Required(
                    CONF_RTSP_PORT, default=options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
