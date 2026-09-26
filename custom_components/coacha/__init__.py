from __future__ import annotations

import aiohttp
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CoachaApiError, CoachaAuthError, CoachaClient, Member, Profile
from .const import CONF_LOGIN_URL
from .coordinator import CoachaConfigEntry, CoachaCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


def create_client(hass: HomeAssistant, entry_data: dict, profile: Profile | None = None) -> CoachaClient:
    return CoachaClient(
        async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar()),
        entry_data[CONF_LOGIN_URL],
        entry_data[CONF_EMAIL],
        entry_data[CONF_PASSWORD],
        profile,
    )


async def async_connect_members(hass: HomeAssistant, entry_data: dict) -> tuple[dict[int, CoachaClient], list[Member]]:
    bootstrap = create_client(hass, entry_data)
    await bootstrap.async_authenticate()
    clients_by_user: dict[int, CoachaClient] = {}
    members: list[Member] = []
    for profile in await bootstrap.async_get_profiles():
        client = create_client(hass, entry_data, profile)
        await client.async_authenticate()
        for member in await client.async_get_members():
            clients_by_user[member.user_id] = client
            members.append(member)
    return clients_by_user, members


async def async_setup_entry(hass: HomeAssistant, entry: CoachaConfigEntry) -> bool:
    try:
        clients_by_user, members = await async_connect_members(hass, dict(entry.data))
    except CoachaAuthError as error:
        raise ConfigEntryAuthFailed(str(error)) from error
    except (CoachaApiError, aiohttp.ClientError) as error:
        raise ConfigEntryNotReady(str(error)) from error

    coordinator = CoachaCoordinator(hass, entry, clients_by_user, members)
    await coordinator.async_load_booking_history()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: CoachaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CoachaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
