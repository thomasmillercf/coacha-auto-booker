from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import BASE_URL, OIDC_CLIENT_ID, OIDC_REDIRECT_URI, OIDC_SCOPE

ANTIFORGERY_TOKEN_PATTERN = re.compile(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"')
TOKEN_RENEWAL_MARGIN_SECONDS = 120

# Coacha's `classBookingOption` enum mapped onto the `paymentMethod` enum its booking endpoint takes.
PAYMENT_METHOD_BY_BOOKING_OPTION = {1: 3, 2: 4, 3: 1, 4: 2, 5: 7, 6: 6, 7: 5}
PAYMENT_METHOD_NONE = 0


class CoachaAuthError(Exception):
    pass


class CoachaApiError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    club_id: int
    system_role: str
    next_of_kin: bool
    user_id: int | None


@dataclass(frozen=True)
class Member:
    user_id: int
    name: str


@dataclass(frozen=True)
class Session:
    class_id: int
    class_type_id: int
    title: str
    start: datetime
    end: datetime
    bookable: bool


@dataclass(frozen=True)
class Booking:
    class_id: int
    user_id: int
    user_name: str
    class_name: str
    start: datetime
    paid: bool
    on_waiting_list: bool
    payment_request_code: str | None


@dataclass(frozen=True)
class ClassAvailability:
    class_id: int
    bookable: bool
    full: bool


def build_payment_url(payment_request_code: str | None) -> str:
    if payment_request_code:
        return f"{BASE_URL}/member_payment?rc={payment_request_code}"
    return f"{BASE_URL}/dashboard"


def format_api_datetime(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class CoachaClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        login_url: str,
        email: str,
        password: str,
        profile: Profile | None = None,
    ) -> None:
        self._session = session
        self._login_url = login_url
        self._email = email
        self._password = password
        self._profile = profile
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def profile(self) -> Profile | None:
        return self._profile

    async def async_authenticate(self) -> None:
        await self._async_submit_login_form()
        await self._async_issue_token()
        if self._profile is not None:
            await self._async_select_profile(self._profile)
            await self._async_issue_token()

    async def async_get_profiles(self) -> list[Profile]:
        selections = await self._async_request_json("GET", "/api/userselection")
        return [
            Profile(
                club_id=selection["clubId"],
                system_role=selection["systemRole"],
                next_of_kin=selection["isNextOfKin"],
                user_id=selection["userId"],
            )
            for selection in selections
        ]

    async def async_get_members(self) -> list[Member]:
        user_info = await self._async_request_json("GET", "/api/userinfo")
        return [
            Member(user_id=user["userId"], name=f"{user['firstName']} {user['lastName']}")
            for user in user_info.get("users") or []
        ]

    async def async_get_sessions(self, start: datetime, end: datetime) -> list[Session]:
        events = await self._async_request_json(
            "GET", f"/api/membercalendar/events/{format_api_datetime(start)}/{format_api_datetime(end)}"
        )
        return [
            Session(
                class_id=int(event["id"]),
                class_type_id=event["extendedProps"]["ClassTypeId"],
                title=event["title"],
                start=datetime.fromisoformat(event["start"]),
                end=datetime.fromisoformat(event["end"]),
                bookable=event["extendedProps"]["Bookable"],
            )
            for event in events
            if event.get("extendedProps", {}).get("Type") == "Class"
        ]

    async def async_get_bookings(self, start: datetime, end: datetime) -> list[Booking]:
        classes = await self._async_request_json(
            "GET",
            f"/api/membercalendar/classes/{format_api_datetime(start)}/{format_api_datetime(end)}",
            params={"timeslotType": "None"},
        )
        return [
            Booking(
                class_id=booked["classId"],
                user_id=booked["userId"],
                user_name=booked["userName"],
                class_name=booked["className"],
                start=datetime.fromisoformat(booked["classStart"]),
                paid=booked["paid"],
                on_waiting_list=booked["onWaitingList"],
                payment_request_code=booked["paymentRequestCode"],
            )
            for booked in classes
        ]

    async def async_get_class_availability(self, class_type_id: int, user_id: int) -> list[ClassAvailability]:
        classes = await self._async_request_json(
            "GET", f"/api/membercalendar/classtype/{class_type_id}/classes", params={"userid": str(user_id)}
        )
        return [
            ClassAvailability(class_id=entry["id"], bookable=entry["bookable"], full=entry["full"]) for entry in classes
        ]

    async def async_get_waiting_list_class_type_ids(self) -> set[int]:
        class_types = await self._async_request_json("GET", "/api/membercalendar/classtypes")
        return {class_type["id"] for class_type in class_types if class_type["waitListEnabled"]}

    async def async_get_payment_method(self, user_id: int, class_id: int) -> int | None:
        options = await self._async_request_json(
            "GET", f"/api/membercalendar/{user_id}/class/{class_id}/bookingoptions"
        )
        enabled_options = [option for option in options or [] if option["enabled"]]
        if not enabled_options:
            return None
        return PAYMENT_METHOD_BY_BOOKING_OPTION.get(enabled_options[0]["classBookingOption"], PAYMENT_METHOD_NONE)

    async def async_book(self, user_id: int, class_id: int, payment_method: int) -> str | None:
        result = await self._async_request_json(
            "POST",
            f"/api/membercalendar/{user_id}/class/{class_id}",
            params={"paymentMethod": str(payment_method), "sendBookingConfirmation": "false"},
            json={},
        )
        return (result or {}).get("paymentRequestCode")

    async def async_join_waiting_list(self, user_id: int, class_id: int) -> None:
        await self._async_request_json("POST", f"/api/membercalendar/{user_id}/class/{class_id}/waitingList", json={})

    async def _async_submit_login_form(self) -> None:
        async with self._session.get(self._login_url) as response:
            response.raise_for_status()
            page = await response.text()
        match = ANTIFORGERY_TOKEN_PATTERN.search(page)
        if match is None:
            raise CoachaApiError("Login page has no anti-forgery token")
        form = {
            "Input.Email": self._email,
            "Input.Password": self._password,
            "__RequestVerificationToken": match.group(1),
        }
        async with self._session.post(self._login_url, data=form) as response:
            response.raise_for_status()

    async def _async_issue_token(self) -> None:
        verifier, challenge = create_pkce_pair()
        params = {
            "client_id": OIDC_CLIENT_ID,
            "redirect_uri": OIDC_REDIRECT_URI,
            "response_type": "code",
            "scope": OIDC_SCOPE,
            "state": secrets.token_hex(8),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        async with self._session.get(f"{BASE_URL}/connect/authorize", params=params, allow_redirects=False) as response:
            location = response.headers.get("Location", "")
        codes = parse_qs(urlparse(location).query).get("code")
        if not codes:
            raise CoachaAuthError("Coacha rejected the email or password")
        token_request = {
            "grant_type": "authorization_code",
            "client_id": OIDC_CLIENT_ID,
            "code": codes[0],
            "redirect_uri": OIDC_REDIRECT_URI,
            "code_verifier": verifier,
        }
        async with self._session.post(f"{BASE_URL}/connect/token", data=token_request) as response:
            response.raise_for_status()
            token = await response.json()
        self._access_token = token["access_token"]
        self._token_expires_at = time.monotonic() + token["expires_in"]

    async def _async_select_profile(self, profile: Profile) -> None:
        selection = {
            "clubId": profile.club_id,
            "systemRole": profile.system_role,
            "nextOfKin": profile.next_of_kin,
            "userId": profile.user_id,
        }
        await self._async_send("POST", "/api/userselection/select", json=selection)

    async def _async_request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._access_token is None or time.monotonic() > self._token_expires_at - TOKEN_RENEWAL_MARGIN_SECONDS:
            await self.async_authenticate()
        try:
            return await self._async_send(method, path, **kwargs)
        except CoachaAuthError:
            await self.async_authenticate()
            return await self._async_send(method, path, **kwargs)

    async def _async_send(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._access_token}"}
        async with self._session.request(method, f"{BASE_URL}{path}", headers=headers, **kwargs) as response:
            if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise CoachaAuthError(f"{method} {path} returned {response.status}")
            if response.status >= HTTPStatus.BAD_REQUEST:
                raise CoachaApiError(f"{method} {path} returned {response.status}: {await response.text()}")
            body = await response.text()
        return json.loads(body) if body else None
