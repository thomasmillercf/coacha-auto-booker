from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Member
from .coordinator import CoachaConfigEntry, CoachaCoordinator
from .entity import CoachaMemberEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CoachaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(CancelBookingButton(coordinator, member) for member in coordinator.members)


class CancelBookingButton(CoachaMemberEntity, ButtonEntity):
    def __init__(self, coordinator: CoachaCoordinator, member: Member) -> None:
        super().__init__(coordinator, member, "cancel_booking")

    async def async_press(self) -> None:
        await self.coordinator.async_cancel_selected(self._member.user_id)
