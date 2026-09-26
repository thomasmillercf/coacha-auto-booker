from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.coacha.api import Booking, ClassAvailability, Member, Session
from custom_components.coacha.const import DOMAIN

ALEX = Member(user_id=1000001, name="Alex Skier")
FRIDAY_ERSA = Session(
    class_id=6872335,
    class_type_id=334825,
    title="BOOK THIS FIRST if on WGCSRC ERSA members list! (2nd Oct 2026)",
    start=datetime(2026, 10, 2, 19),
    end=datetime(2026, 10, 2, 21),
    bookable=True,
)
FRIDAY_MEMBERS = Session(
    class_id=6872334,
    class_type_id=334824,
    title="MEMBERS (2nd Oct 2026)",
    start=datetime(2026, 10, 2, 19),
    end=datetime(2026, 10, 2, 21),
    bookable=True,
)


def build_booking(class_id: int, paid: bool = True) -> Booking:
    return Booking(
        class_id=class_id,
        user_id=ALEX.user_id,
        user_name=ALEX.name,
        class_name=FRIDAY_MEMBERS.title,
        start=FRIDAY_MEMBERS.start,
        paid=paid,
        on_waiting_list=False,
        payment_request_code=None,
    )


def build_client(bookings: list[list[Booking]], full_class_ids: frozenset[int] = frozenset()) -> MagicMock:
    client = MagicMock()
    client.async_get_sessions = AsyncMock(return_value=[FRIDAY_MEMBERS, FRIDAY_ERSA])
    client.async_get_bookings = AsyncMock(side_effect=bookings)
    client.async_get_waiting_list_class_type_ids = AsyncMock(
        return_value={FRIDAY_MEMBERS.class_type_id, FRIDAY_ERSA.class_type_id}
    )
    client.async_get_class_availability = AsyncMock(
        side_effect=lambda class_type_id, user_id: [
            ClassAvailability(class_id=session.class_id, bookable=True, full=session.class_id in full_class_ids)
            for session in (FRIDAY_MEMBERS, FRIDAY_ERSA)
            if session.class_type_id == class_type_id
        ]
    )
    client.async_get_payment_method = AsyncMock(return_value=1)
    client.async_book = AsyncMock(return_value="pay-me")
    client.async_join_waiting_list = AsyncMock()
    return client


async def set_up(hass: HomeAssistant, client: MagicMock) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/London")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"login_url": "https://my.coacha.app/login/X", "email": "me@example.com", "password": "secret"},
        options={
            "session_types": {str(ALEX.user_id): ["BOOK THIS FIRST if on WGCSRC ERSA members list!", "MEMBERS"]},
            "weekdays": ["friday"],
            "waiting_list": True,
            "scan_interval": 300,
            "notify_service": "notify.mobile_app_phone",
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.coacha.async_connect_members",
        AsyncMock(return_value=({ALEX.user_id: client}, [ALEX])),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.fixture
def phone_notifications(hass: HomeAssistant):
    return async_mock_service(hass, "notify", "mobile_app_phone")


class TestCoachaCoordinator:
    async def test_books_only_the_ersa_session_and_sends_the_payment_link(self, hass, phone_notifications):
        client = build_client([[], [build_booking(FRIDAY_ERSA.class_id, paid=False)]])

        await set_up(hass, client)

        client.async_book.assert_awaited_once_with(ALEX.user_id, FRIDAY_ERSA.class_id, 1)
        assert phone_notifications[0].data["data"]["url"] == "https://my.coacha.app/member_payment?rc=pay-me"
        assert hass.states.get("sensor.coacha_alex_unpaid_bookings").state == "1"

    async def test_books_members_when_ersa_is_full(self, hass, phone_notifications):
        client = build_client([[], []], full_class_ids=frozenset({FRIDAY_ERSA.class_id}))

        await set_up(hass, client)

        client.async_book.assert_awaited_once_with(ALEX.user_id, FRIDAY_MEMBERS.class_id, 1)
        client.async_join_waiting_list.assert_not_awaited()

    async def test_joins_the_ersa_waiting_list_when_both_are_full(self, hass, phone_notifications):
        client = build_client([[], []], full_class_ids=frozenset({FRIDAY_ERSA.class_id, FRIDAY_MEMBERS.class_id}))

        await set_up(hass, client)

        client.async_join_waiting_list.assert_awaited_once_with(ALEX.user_id, FRIDAY_ERSA.class_id)
        client.async_book.assert_not_awaited()

    async def test_never_rebooks_a_cancelled_session(self, hass, phone_notifications):
        client = build_client([[build_booking(FRIDAY_MEMBERS.class_id)], []])
        entry = await set_up(hass, client)

        await entry.runtime_data.async_refresh()

        client.async_book.assert_not_awaited()

    async def test_does_not_book_when_auto_book_is_off(self, hass, phone_notifications):
        client = build_client([[], [], []])
        entry = await set_up(hass, client)
        client.async_book.reset_mock()

        await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.coacha_auto_book"}, blocking=True)
        await entry.runtime_data.async_refresh()

        client.async_book.assert_not_awaited()

    async def test_pauses_checks_until_the_booked_friday_is_over(self, hass, phone_notifications, freezer):
        freezer.move_to("2026-09-28 09:00:00+01:00")
        booked = [build_booking(FRIDAY_ERSA.class_id)]
        client = build_client([booked, booked])

        entry = await set_up(hass, client)

        assert entry.runtime_data.update_interval == timedelta(days=4, hours=12)
        client.async_book.assert_not_awaited()

    async def test_checks_every_five_minutes_while_waiting_for_a_place(self, hass, phone_notifications, freezer):
        freezer.move_to("2026-09-28 09:00:00+01:00")
        client = build_client([[], []], full_class_ids=frozenset({FRIDAY_ERSA.class_id, FRIDAY_MEMBERS.class_id}))
        client.async_get_waiting_list_class_type_ids = AsyncMock(return_value=set())

        entry = await set_up(hass, client)

        assert entry.runtime_data.update_interval == timedelta(minutes=5)
