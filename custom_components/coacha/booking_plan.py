from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .api import Booking, Session

TRAILING_BRACKETED_PATTERN = re.compile(r"\s*\([^)]*\)\s*$")
DATE_PATTERN = re.compile(
    r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b",
    re.IGNORECASE,
)
DETAIL_SEPARATOR = " - "


@dataclass(frozen=True)
class PlannedDay:
    user_id: int
    day: date
    candidates: tuple[Session, ...]


def normalise_session_type(title: str) -> str:
    without_detail = title.split(DETAIL_SEPARATOR, 1)[0]
    without_bracket = TRAILING_BRACKETED_PATTERN.sub("", without_detail)
    without_date = DATE_PATTERN.sub("", without_bracket)
    return " ".join(without_date.split())


def rank_session_type(title: str, wanted_types: Sequence[str]) -> int | None:
    session_type = normalise_session_type(title).casefold()
    folded_wanted = [" ".join(wanted.split()).casefold() for wanted in wanted_types]
    return folded_wanted.index(session_type) if session_type in folded_wanted else None


def find_taken_days(
    sessions: Sequence[Session], bookings: Iterable[Booking], blocked: Collection[tuple[int, int]]
) -> set[tuple[int, date]]:
    session_days = {session.class_id: session.start.date() for session in sessions}
    taken_days = {(booking.user_id, booking.start.date()) for booking in bookings}
    return taken_days | {(user_id, session_days[class_id]) for user_id, class_id in blocked if class_id in session_days}


def find_idle_until(
    sessions: Iterable[Session],
    bookings: Iterable[Booking],
    wanted_types_by_user: Mapping[int, Sequence[str]],
    weekdays: Collection[int],
    blocked: Collection[tuple[int, int]],
    now: datetime,
) -> datetime | None:
    upcoming = [
        session
        for session in sessions
        if session.end > now
        and session.start.weekday() in weekdays
        and any(rank_session_type(session.title, wanted) is not None for wanted in wanted_types_by_user.values())
    ]
    if not upcoming or not wanted_types_by_user:
        return None
    next_day = min(session.start.date() for session in upcoming)
    taken_days = find_taken_days(upcoming, bookings, blocked)
    if any((user_id, next_day) not in taken_days for user_id in wanted_types_by_user):
        return None
    return max(session.end for session in upcoming if session.start.date() == next_day)


def plan_bookings(
    sessions: Iterable[Session],
    bookings: Iterable[Booking],
    wanted_types_by_user: Mapping[int, Sequence[str]],
    weekdays: Collection[int],
    blocked: Collection[tuple[int, int]],
) -> list[PlannedDay]:
    sessions = list(sessions)
    taken_days = find_taken_days(sessions, bookings, blocked)
    planned: list[PlannedDay] = []
    for user_id, wanted_types in wanted_types_by_user.items():
        ranked_by_day: dict[date, list[tuple[int, Session]]] = defaultdict(list)
        for session in sessions:
            rank = rank_session_type(session.title, wanted_types)
            day = session.start.date()
            if rank is None or not session.bookable or day.weekday() not in weekdays:
                continue
            if (user_id, day) not in taken_days:
                ranked_by_day[day].append((rank, session))
        planned.extend(
            PlannedDay(
                user_id=user_id,
                day=day,
                candidates=tuple(
                    session for _, session in sorted(ranked, key=lambda ranked_session: ranked_session[0])
                ),
            )
            for day, ranked in sorted(ranked_by_day.items())
        )
    return planned
