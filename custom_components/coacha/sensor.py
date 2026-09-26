from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Booking, Member, build_payment_url
from .coordinator import CoachaConfigEntry, CoachaCoordinator
from .entity import CoachaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CoachaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [LastActionSensor(coordinator), NextCheckSensor(coordinator)]
    for member in coordinator.members:
        entities.append(NextBookingSensor(coordinator, member))
        entities.append(UnpaidBookingsSensor(coordinator, member))
    async_add_entities(entities)


def describe_booking(booking: Booking) -> dict[str, Any]:
    return {
        "session": booking.class_name,
        "start": booking.start.isoformat(),
        "paid": booking.paid,
        "waiting_list": booking.on_waiting_list,
        "payment_url": build_payment_url(booking.payment_request_code),
    }


class MemberSensor(CoachaEntity, SensorEntity):
    def __init__(self, coordinator: CoachaCoordinator, member: Member, key: str) -> None:
        super().__init__(coordinator, key)
        self._member = member
        self._attr_unique_id = f"{self._attr_unique_id}_{member.user_id}"
        self._attr_translation_placeholders = {"member": member.name.split()[0]}

    @property
    def member_bookings(self) -> list[Booking]:
        return sorted(
            (booking for booking in self.coordinator.data.bookings if booking.user_id == self._member.user_id),
            key=lambda booking: booking.start,
        )


class NextBookingSensor(MemberSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: CoachaCoordinator, member: Member) -> None:
        super().__init__(coordinator, member, "next_booking")

    @property
    def native_value(self) -> datetime | None:
        upcoming = [booking for booking in self.member_bookings if not booking.on_waiting_list]
        return dt_util.as_utc(upcoming[0].start) if upcoming else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"bookings": [describe_booking(booking) for booking in self.member_bookings]}


class UnpaidBookingsSensor(MemberSensor):
    def __init__(self, coordinator: CoachaCoordinator, member: Member) -> None:
        super().__init__(coordinator, member, "unpaid_bookings")

    @property
    def unpaid_bookings(self) -> list[Booking]:
        return [booking for booking in self.member_bookings if not booking.paid and not booking.on_waiting_list]

    @property
    def native_value(self) -> int:
        return len(self.unpaid_bookings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"bookings": [describe_booking(booking) for booking in self.unpaid_bookings]}


class LastActionSensor(CoachaEntity, SensorEntity):
    def __init__(self, coordinator: CoachaCoordinator) -> None:
        super().__init__(coordinator, "last_action")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.last_action

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last_action_at = self.coordinator.data.last_action_at
        return {"at": last_action_at.isoformat() if last_action_at else None}


class NextCheckSensor(CoachaEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: CoachaCoordinator) -> None:
        super().__init__(coordinator, "next_check")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.next_check_at
