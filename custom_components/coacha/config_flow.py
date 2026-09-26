from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import async_connect_members
from .api import CoachaApiError, CoachaAuthError, Member
from .const import (
    CONF_LOGIN_URL,
    CONF_NOTIFY_SERVICE,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_TYPES,
    CONF_WAITING_LIST,
    CONF_WEEKDAYS,
    DEFAULT_LOGIN_URL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    MINIMUM_SCAN_INTERVAL_SECONDS,
    WEEKDAY_NAMES,
)
from .coordinator import CoachaConfigEntry

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOGIN_URL, default=DEFAULT_LOGIN_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


class CoachaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].casefold())
            self._abort_if_unique_id_configured()
            try:
                _, members = await async_connect_members(self.hass, user_input)
            except CoachaAuthError:
                errors["base"] = "invalid_auth"
            except (CoachaApiError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                if not members:
                    errors["base"] = "no_members"
                else:
                    return self.async_create_entry(title=user_input[CONF_EMAIL], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: CoachaConfigEntry) -> CoachaOptionsFlow:
        return CoachaOptionsFlow()


class CoachaOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._session_types: dict[str, list[str]] = {}
        self._pending_members: list[Member] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            self._options = {
                CONF_WEEKDAYS: user_input[CONF_WEEKDAYS],
                CONF_WAITING_LIST: user_input[CONF_WAITING_LIST],
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_NOTIFY_SERVICE: user_input.get(CONF_NOTIFY_SERVICE, ""),
            }
            self._pending_members = list(self.config_entry.runtime_data.members)
            return await self.async_step_member()
        schema = vol.Schema(
            {
                vol.Required(CONF_WEEKDAYS, default=options.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)): SelectSelector(
                    SelectSelectorConfig(options=WEEKDAY_NAMES, multiple=True, translation_key=CONF_WEEKDAYS)
                ),
                vol.Required(CONF_WAITING_LIST, default=options.get(CONF_WAITING_LIST, True)): BooleanSelector(),
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MINIMUM_SCAN_INTERVAL_SECONDS,
                        max=3600,
                        step=5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_SERVICE, description={"suggested_value": options.get(CONF_NOTIFY_SERVICE, "")}
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_member(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            member = self._pending_members.pop(0)
            self._session_types[str(member.user_id)] = user_input.get(CONF_SESSION_TYPES, [])
        if not self._pending_members:
            return self.async_create_entry(data={**self._options, CONF_SESSION_TYPES: self._session_types})

        member = self._pending_members[0]
        saved_types: dict[str, list[str]] = self.config_entry.options.get(CONF_SESSION_TYPES, {})
        member_saved_types = saved_types.get(str(member.user_id), [])
        known_types = sorted(
            set(self.config_entry.runtime_data.data.session_types)
            | {wanted for wanted_types in saved_types.values() for wanted in wanted_types}
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_SESSION_TYPES, description={"suggested_value": member_saved_types}): SelectSelector(
                    SelectSelectorConfig(options=known_types, multiple=True, custom_value=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="member", data_schema=schema, description_placeholders={"member": member.name}
        )
