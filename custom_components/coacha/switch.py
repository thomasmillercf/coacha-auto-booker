from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import CoachaConfigEntry, CoachaCoordinator
from .entity import CoachaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CoachaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AutoBookSwitch(entry.runtime_data)])


class AutoBookSwitch(CoachaEntity, SwitchEntity, RestoreEntity):
    def __init__(self, coordinator: CoachaCoordinator) -> None:
        super().__init__(coordinator, "auto_book")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self.coordinator.auto_book_enabled = last_state.state != STATE_OFF

    @property
    def is_on(self) -> bool:
        return self.coordinator.auto_book_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_book_enabled = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_book_enabled = False
        self.async_write_ha_state()
