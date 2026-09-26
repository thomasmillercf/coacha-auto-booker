from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Booking,
    CoachaApiError,
    CoachaAuthError,
    CoachaClient,
    Member,
    Session,
    build_payment_url,
)
from .booking_plan import PlannedDay, find_idle_until, normalise_session_type, plan_bookings, rank_session_type
from .const import (
    CONF_NOTIFY_SERVICE,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_TYPES,
    CONF_WAITING_LIST,
    CONF_WEEKDAYS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    EVENT_BOOKED,
    EVENT_SESSION_AVAILABLE,
    LOOKAHEAD,
    WEEKDAY_NAMES,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type CoachaConfigEntry = ConfigEntry[CoachaCoordinator]


@dataclass
class CoachaData:
    sessions: list[Session] = field(default_factory=list)
    bookings: list[Booking] = field(default_factory=list)
    last_action: str | None = None
    last_action_at: datetime | None = None
    next_check_at: datetime | None = None

    @property
    def session_types(self) -> list[str]:
        return sorted({normalise_session_type(session.title) for session in self.sessions})


class CoachaCoordinator(DataUpdateCoordinator[CoachaData]):
    config_entry: CoachaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CoachaConfigEntry,
        clients_by_user: dict[int, CoachaClient],
        members: list[Member],
    ) -> None:
        scan_interval = timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self._scan_interval = scan_interval
        self.clients_by_user = clients_by_user
        self.members = members
        self.auto_book_enabled = True
        self._failed_attempts: set[tuple[int, int]] = set()
        self._ever_booked: set[tuple[int, int]] = set()
        self._announced_class_ids: set[int] | None = None
        self._store: Store[dict[str, list]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.ever_booked")
        self._last_action: str | None = None
        self._last_action_at: datetime | None = None

    @property
    def member_names(self) -> dict[int, str]:
        return {member.user_id: member.name for member in self.members}

    @property
    def wanted_types_by_user(self) -> dict[int, list[str]]:
        configured = self.config_entry.options.get(CONF_SESSION_TYPES, {})
        return {
            int(user_id): wanted_types
            for user_id, wanted_types in configured.items()
            if wanted_types and int(user_id) in self.clients_by_user
        }

    @property
    def weekdays(self) -> set[int]:
        return {WEEKDAY_NAMES.index(name) for name in self.config_entry.options.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)}

    async def async_load_booking_history(self) -> None:
        stored = await self._store.async_load() or {}
        self._ever_booked = {(user_id, class_id) for user_id, class_id in stored.get("ever_booked", [])}
        if "announced" in stored:
            self._announced_class_ids = set(stored["announced"])

    async def _async_save_history(self) -> None:
        await self._store.async_save(
            {
                "ever_booked": sorted([list(key) for key in self._ever_booked]),
                "announced": sorted(self._announced_class_ids or set()),
            }
        )

    async def _async_remember_bookings(self, bookings: list[Booking]) -> None:
        seen = {(booking.user_id, booking.class_id) for booking in bookings}
        if seen <= self._ever_booked:
            return
        self._ever_booked |= seen
        await self._async_save_history()

    async def _async_announce_available(self, sessions: list[Session]) -> None:
        wanted_types = list(self.wanted_types_by_user.values())
        available = {
            session.class_id: session
            for session in sessions
            if session.bookable
            and session.start.weekday() in self.weekdays
            and any(rank_session_type(session.title, wanted) is not None for wanted in wanted_types)
        }
        if self._announced_class_ids is None:
            self._announced_class_ids = set(available)
            await self._async_save_history()
            return
        newly_available = [
            session for class_id, session in available.items() if class_id not in self._announced_class_ids
        ]
        if not newly_available:
            return
        for session in newly_available:
            self.hass.bus.async_fire(
                EVENT_SESSION_AVAILABLE,
                {
                    "class_id": session.class_id,
                    "session": session.title,
                    "session_type": normalise_session_type(session.title),
                    "start": session.start.isoformat(),
                },
            )
        self._announced_class_ids |= set(available)
        await self._async_save_history()

    async def _async_update_data(self) -> CoachaData:
        start = dt_util.utcnow()
        end = start + LOOKAHEAD
        try:
            sessions = await self._primary_client.async_get_sessions(start, end)
            bookings = await self._async_get_all_bookings(start, end)
            await self._async_remember_bookings(bookings)
            await self._async_announce_available(sessions)
            if self.auto_book_enabled:
                planned = plan_bookings(
                    sessions,
                    bookings,
                    self.wanted_types_by_user,
                    self.weekdays,
                    self._ever_booked | self._failed_attempts,
                )
                if planned:
                    await self._async_carry_out(planned)
                    bookings = await self._async_get_all_bookings(start, end)
                    await self._async_remember_bookings(bookings)
        except CoachaAuthError as error:
            raise UpdateFailed(f"Coacha login failed: {error}") from error
        except CoachaApiError as error:
            raise UpdateFailed(str(error)) from error
        next_check_at = self._schedule_next_check(sessions, bookings)
        return CoachaData(
            sessions=sessions,
            bookings=bookings,
            last_action=self._last_action,
            last_action_at=self._last_action_at,
            next_check_at=next_check_at,
        )

    def _schedule_next_check(self, sessions: list[Session], bookings: list[Booking]) -> datetime:
        now = dt_util.now()
        idle_until = find_idle_until(
            sessions,
            bookings,
            self.wanted_types_by_user,
            self.weekdays,
            self._ever_booked | self._failed_attempts,
            now.replace(tzinfo=None),
        )
        if idle_until is None:
            self.update_interval = self._scan_interval
        else:
            idle_until = idle_until.replace(tzinfo=dt_util.get_default_time_zone())
            self.update_interval = max(idle_until - now, self._scan_interval)
        return now + self.update_interval

    @property
    def _primary_client(self) -> CoachaClient:
        return next(iter(self.clients_by_user.values()))

    async def _async_get_all_bookings(self, start: datetime, end: datetime) -> list[Booking]:
        bookings_by_key: dict[tuple[int, int], Booking] = {}
        for client in set(self.clients_by_user.values()):
            for booking in await client.async_get_bookings(start, end):
                bookings_by_key[(booking.user_id, booking.class_id)] = booking
        return list(bookings_by_key.values())

    async def _async_carry_out(self, planned: list[PlannedDay]) -> None:
        waiting_list_type_ids = await self._primary_client.async_get_waiting_list_class_type_ids()
        for plan in planned:
            await self._async_book_one_session(plan, waiting_list_type_ids)

    async def _async_book_one_session(self, plan: PlannedDay, waiting_list_type_ids: set[int]) -> None:
        client = self.clients_by_user[plan.user_id]
        first_full_session: Session | None = None
        for session in plan.candidates:
            try:
                availability = {
                    entry.class_id: entry
                    for entry in await client.async_get_class_availability(session.class_type_id, plan.user_id)
                }.get(session.class_id)
                if availability is None or not availability.bookable:
                    continue
                if availability.full:
                    if first_full_session is None and session.class_type_id in waiting_list_type_ids:
                        first_full_session = session
                    continue
                payment_method = await client.async_get_payment_method(plan.user_id, session.class_id)
                if payment_method is None:
                    continue
                payment_request_code = await client.async_book(plan.user_id, session.class_id, payment_method)
            except CoachaApiError as error:
                await self._async_record_failure(plan.user_id, session, error)
                continue
            await self._async_record(plan.user_id, session, "booked", payment_request_code)
            return

        if first_full_session is None or not self.config_entry.options.get(CONF_WAITING_LIST, True):
            return
        try:
            await client.async_join_waiting_list(plan.user_id, first_full_session.class_id)
        except CoachaApiError as error:
            await self._async_record_failure(plan.user_id, first_full_session, error)
            return
        await self._async_record(plan.user_id, first_full_session, "waiting_list", None)

    async def _async_record_failure(self, user_id: int, session: Session, error: CoachaApiError) -> None:
        self._failed_attempts.add((user_id, session.class_id))
        _LOGGER.warning("Booking %s for %s failed: %s", session.title, user_id, error)
        self.hass.bus.async_fire(
            EVENT_BOOKED, {**self._describe(user_id, session, build_payment_url(None)), "outcome": "failed"}
        )
        await self._async_notify(
            "Coacha booking failed",
            f"Could not book {session.title} for {self.member_names[user_id]}: {error}",
            build_payment_url(None),
        )

    def _describe(self, user_id: int, session: Session, payment_url: str) -> dict[str, str | int]:
        return {
            "user_id": user_id,
            "member": self.member_names[user_id],
            "class_id": session.class_id,
            "session": session.title,
            "start": session.start.isoformat(),
            "payment_url": payment_url,
        }

    async def _async_record(
        self, user_id: int, session: Session, outcome: str, payment_request_code: str | None
    ) -> None:
        name = self.member_names[user_id]
        payment_url = build_payment_url(payment_request_code)
        when = session.start.strftime("%a %-d %b %H:%M")
        if outcome == "booked":
            title = f"Booked {name} on Coacha"
            message = f"{session.title} ({when}). Pay here: {payment_url}"
        else:
            title = f"{name} is on the Coacha waiting list"
            message = f"{session.title} ({when}) was full."
        self._last_action = f"{title}: {session.title}"
        self._last_action_at = dt_util.utcnow()
        self.hass.bus.async_fire(EVENT_BOOKED, {**self._describe(user_id, session, payment_url), "outcome": outcome})
        await self._async_notify(title, message, payment_url)

    async def _async_notify(self, title: str, message: str, url: str) -> None:
        persistent_notification.async_create(self.hass, message, title=title)
        notify_service = self.config_entry.options.get(CONF_NOTIFY_SERVICE)
        if not notify_service:
            return
        service = notify_service.removeprefix("notify.")
        await self.hass.services.async_call(
            "notify",
            service,
            {"title": title, "message": message, "data": {"url": url, "clickAction": url}},
            blocking=False,
        )
