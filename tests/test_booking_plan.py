from datetime import datetime

from custom_components.coacha.api import Booking, Session
from custom_components.coacha.booking_plan import (
    PlannedDay,
    find_idle_until,
    normalise_session_type,
    plan_bookings,
    rank_session_type,
)

FRIDAY = {4}
ERSA = "BOOK THIS FIRST if on WGCSRC ERSA members list!"
MEMBERS = "MEMBERS"
PREFERENCES = [ERSA, MEMBERS]

ERSA_SESSION = Session(
    class_id=6872335,
    class_type_id=334825,
    title="BOOK THIS FIRST if on WGCSRC ERSA members list! (2nd Oct 2026)",
    start=datetime(2026, 10, 2, 19),
    end=datetime(2026, 10, 2, 21),
    bookable=True,
)
MEMBERS_SESSION = Session(
    class_id=6872334,
    class_type_id=334824,
    title="MEMBERS (2nd Oct 2026)",
    start=datetime(2026, 10, 2, 19),
    end=datetime(2026, 10, 2, 21),
    bookable=True,
)
MONDAY_SESSION = Session(
    class_id=6861105,
    class_type_id=334256,
    title="Milton Keynes Training Session 12th October 2026 - PLEASE ARRIVE 15 MINS BEFORE THE SESSION STARTS",
    start=datetime(2026, 10, 12, 19),
    end=datetime(2026, 10, 12, 21),
    bookable=True,
)


def build_booking(user_id: int, class_id: int) -> Booking:
    return Booking(
        class_id=class_id,
        user_id=user_id,
        user_name="Alex Skier",
        class_name="MEMBERS (2nd Oct 2026)",
        start=datetime.fromisoformat("2026-10-02T19:00:00+01:00"),
        paid=True,
        on_waiting_list=False,
        payment_request_code=None,
    )


class TestBookingPlan:
    class TestNormaliseSessionType:
        def test_drops_the_bracketed_date(self):
            assert normalise_session_type("MEMBERS (2nd Oct 2026)") == "MEMBERS"

        def test_drops_the_inline_date_and_detail(self):
            assert normalise_session_type(MONDAY_SESSION.title) == "Milton Keynes Training Session"

    class TestRankSessionType:
        def test_ranks_by_preference_order(self):
            assert rank_session_type(MEMBERS_SESSION.title, PREFERENCES) == 1

        def test_matches_case_insensitively(self):
            assert rank_session_type(MEMBERS_SESSION.title, ["members"]) == 0

        def test_does_not_match_a_type_that_only_contains_the_name(self):
            assert rank_session_type(ERSA_SESSION.title, [MEMBERS]) is None

    class TestPlanBookings:
        def test_puts_the_ersa_session_first(self):
            assert plan_bookings([MEMBERS_SESSION, ERSA_SESSION], [], {1: PREFERENCES}, FRIDAY, set()) == [
                PlannedDay(user_id=1, day=ERSA_SESSION.start.date(), candidates=(ERSA_SESSION, MEMBERS_SESSION))
            ]

        def test_skips_a_day_the_member_already_has_a_session_on(self):
            bookings = [build_booking(1, MEMBERS_SESSION.class_id)]

            assert plan_bookings([ERSA_SESSION, MEMBERS_SESSION], bookings, {1: PREFERENCES}, FRIDAY, set()) == []

        def test_skips_a_day_with_a_cancelled_session(self):
            blocked = {(1, MEMBERS_SESSION.class_id)}

            assert plan_bookings([ERSA_SESSION, MEMBERS_SESSION], [], {1: PREFERENCES}, FRIDAY, blocked) == []

        def test_skips_an_unbookable_session(self):
            unbookable = Session(
                class_id=1,
                class_type_id=2,
                title="MEMBERS",
                start=datetime(2026, 10, 2),
                end=datetime(2026, 10, 2, 2),
                bookable=False,
            )

            assert plan_bookings([unbookable], [], {1: [MEMBERS]}, FRIDAY, set()) == []

        def test_skips_a_session_on_an_unwanted_day(self):
            assert plan_bookings([MONDAY_SESSION], [], {1: ["Milton Keynes Training Session"]}, FRIDAY, set()) == []

        def test_plans_each_member_separately(self):
            plans = plan_bookings(
                [ERSA_SESSION, MEMBERS_SESSION],
                [build_booking(1, 6872335)],
                {1: PREFERENCES, 2: [MEMBERS]},
                FRIDAY,
                set(),
            )

            assert plans == [PlannedDay(user_id=2, day=MEMBERS_SESSION.start.date(), candidates=(MEMBERS_SESSION,))]

    class TestFindIdleUntil:
        before_friday = datetime(2026, 9, 28, 9)

        def test_waits_until_the_session_ends_once_everyone_has_a_place(self):
            bookings = [build_booking(1, ERSA_SESSION.class_id)]

            idle_until = find_idle_until(
                [ERSA_SESSION, MEMBERS_SESSION], bookings, {1: PREFERENCES}, FRIDAY, set(), self.before_friday
            )

            assert idle_until == datetime(2026, 10, 2, 21)

        def test_keeps_checking_while_someone_has_no_place(self):
            bookings = [build_booking(1, ERSA_SESSION.class_id)]

            idle_until = find_idle_until(
                [ERSA_SESSION, MEMBERS_SESSION],
                bookings,
                {1: PREFERENCES, 2: [MEMBERS]},
                FRIDAY,
                set(),
                self.before_friday,
            )

            assert idle_until is None

        def test_counts_a_cancelled_day_as_done(self):
            idle_until = find_idle_until(
                [ERSA_SESSION], [], {1: PREFERENCES}, FRIDAY, {(1, ERSA_SESSION.class_id)}, self.before_friday
            )

            assert idle_until == datetime(2026, 10, 2, 21)

        def test_keeps_checking_when_no_session_is_released(self):
            assert find_idle_until([], [], {1: PREFERENCES}, FRIDAY, set(), self.before_friday) is None

        def test_ignores_a_session_that_has_ended(self):
            after_friday = datetime(2026, 10, 2, 22)
            bookings = [build_booking(1, ERSA_SESSION.class_id)]

            assert find_idle_until([ERSA_SESSION], bookings, {1: PREFERENCES}, FRIDAY, set(), after_friday) is None
