from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Booking, Member
from .booking_plan import normalise_session_type
from .coordinator import CoachaConfigEntry, CoachaCoordinator
from .entity import CoachaMemberEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CoachaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BookingToCancelSelect(coordinator, member) for member in coordinator.members)


def label_bookings(bookings: list[Booking]) -> dict[str, int]:
    labels: dict[str, int] = {}
    for booking in bookings:
        label = f"{booking.start:%a %-d %b %H:%M} {normalise_session_type(booking.class_name)}"
        if label in labels:
            label = f"{label} #{booking.class_id}"
        labels[label] = booking.class_id
    return labels


class BookingToCancelSelect(CoachaMemberEntity, SelectEntity):
    def __init__(self, coordinator: CoachaCoordinator, member: Member) -> None:
        super().__init__(coordinator, member, "booking_to_cancel")

    @property
    def _labels(self) -> dict[str, int]:
        return label_bookings(self.coordinator.cancellable_bookings(self._member.user_id))

    @property
    def options(self) -> list[str]:
        return list(self._labels)

    @property
    def current_option(self) -> str | None:
        selected = self.coordinator.selected_for_cancel.get(self._member.user_id)
        return next((label for label, class_id in self._labels.items() if class_id == selected), None)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.selected_for_cancel[self._member.user_id] = self._labels[option]
        self.async_write_ha_state()
