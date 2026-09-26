from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.coacha.api import CoachaAuthError, CoachaClient, Profile

LOGIN_URL = "https://my.coacha.app/login/WGCSKIRACECLUBLOGIN"
LOGIN_PAGE = '<input name="__RequestVerificationToken" type="hidden" value="antiforgery" />'
AUTHORIZE_URL = "https://my.coacha.app/connect/authorize"
CALLBACK = "https://my.coacha.app/authentication/login-callback?code=the-code&state=x"
PROFILE = Profile(club_id=11005, system_role="ClubMember", next_of_kin=False, user_id=1000001)


def mock_login(aioclient_mock: AiohttpClientMocker, callback: str = CALLBACK) -> None:
    aioclient_mock.get(LOGIN_URL, text=LOGIN_PAGE)
    aioclient_mock.post(LOGIN_URL, text="ok")
    aioclient_mock.get(AUTHORIZE_URL, status=302, headers={"Location": callback})
    aioclient_mock.post("https://my.coacha.app/connect/token", json={"access_token": "the-token", "expires_in": 3600})
    aioclient_mock.post("https://my.coacha.app/api/userselection/select", text="")


def build_client(hass: HomeAssistant, profile: Profile | None = PROFILE) -> CoachaClient:
    return CoachaClient(async_get_clientsession(hass), LOGIN_URL, "me@example.com", "secret", profile)


class TestCoachaClient:
    async def test_authenticates_and_selects_the_profile(self, hass, aioclient_mock):
        mock_login(aioclient_mock)

        await build_client(hass).async_authenticate()

        login_form = next(
            call[2] for call in aioclient_mock.mock_calls if call[0] == "POST" and str(call[1]) == LOGIN_URL
        )
        selection = next(call[2] for call in aioclient_mock.mock_calls if str(call[1]).endswith("/select"))
        assert login_form == {
            "Input.Email": "me@example.com",
            "Input.Password": "secret",
            "__RequestVerificationToken": "antiforgery",
        }
        assert selection == {"clubId": 11005, "systemRole": "ClubMember", "nextOfKin": False, "userId": 1000001}

    async def test_rejects_a_login_without_an_authorisation_code(self, hass, aioclient_mock):
        mock_login(aioclient_mock, callback="https://my.coacha.app/login")

        with pytest.raises(CoachaAuthError):
            await build_client(hass).async_authenticate()

    async def test_books_a_class_by_card(self, hass, aioclient_mock):
        mock_login(aioclient_mock)
        aioclient_mock.post(
            "https://my.coacha.app/api/membercalendar/1000001/class/6872334",
            json={"paymentRequestCode": "pay-me"},
        )

        payment_request_code = await build_client(hass).async_book(1000001, 6872334, 1)

        booking_call = aioclient_mock.mock_calls[-1]
        assert payment_request_code == "pay-me"
        assert str(booking_call[1]) == (
            "https://my.coacha.app/api/membercalendar/1000001/class/6872334"
            "?paymentMethod=1&sendBookingConfirmation=false"
        )
        assert booking_call[3]["Authorization"] == "Bearer the-token"

    async def test_maps_the_card_booking_option_to_stripe(self, hass, aioclient_mock):
        mock_login(aioclient_mock)
        aioclient_mock.get(
            "https://my.coacha.app/api/membercalendar/1000001/class/6872334/bookingoptions",
            json=[{"classBookingOption": 3, "enabled": True}],
        )

        assert await build_client(hass).async_get_payment_method(1000001, 6872334) == 1

    async def test_reads_calendar_classes_only(self, hass, aioclient_mock):
        mock_login(aioclient_mock)
        aioclient_mock.get(
            "https://my.coacha.app/api/membercalendar/events/2026-09-26T00:00:00.000Z/2026-12-25T00:00:00.000Z",
            json=[
                {
                    "id": "6872334",
                    "title": "MEMBERS (2nd Oct 2026)",
                    "start": "2026-10-02T19:00:00",
                    "end": "2026-10-02T21:00:00",
                    "extendedProps": {"Bookable": True, "ClassTypeId": 334824, "Type": "Class"},
                },
                {"id": "9", "title": "AGM", "start": "2026-10-03T19:00:00", "extendedProps": {"Type": "Event"}},
            ],
        )

        sessions = await build_client(hass).async_get_sessions(
            datetime(2026, 9, 26, tzinfo=UTC), datetime(2026, 12, 25, tzinfo=UTC)
        )

        assert [(session.class_id, session.class_type_id, session.bookable) for session in sessions] == [
            (6872334, 334824, True)
        ]

    async def test_logs_in_again_when_the_token_is_refused(self, hass, aioclient_mock):
        mock_login(aioclient_mock)
        aioclient_mock.get("https://my.coacha.app/api/userinfo", status=401)

        with pytest.raises(CoachaAuthError):
            await build_client(hass).async_get_members()

        token_requests = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith("/connect/token")]
        assert len(token_requests) == 4
