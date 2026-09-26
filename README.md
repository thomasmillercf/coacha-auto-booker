# Coacha Auto Booker

Club training sessions on [Coacha](https://my.coacha.app) fill up minutes after they open, often overnight.

This Home Assistant integration watches the club calendar and books the sessions you want as soon as they become bookable. It then sends the payment link to your phone, so you can pay whenever you like.

## Install

1. In HACS, open **Custom repositories** and add `https://github.com/thomasmillercf/coacha-auto-booker` as an **Integration**.
2. Install **Coacha Auto Booker** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Coacha Auto Booker**, and sign in with your club login page, email and password.
4. Open the integration's **Configure** screen to choose what to book.

## Configure

- **Days to book:** only sessions on these days are booked. Defaults to Friday.
- **Waiting list:** joins the waiting list when every wanted session that day is full.
- **Check every:** how often the calendar is polled while a place is still needed. Defaults to 5 minutes.
- **Notify service:** where booking alerts go, e.g. `notify.mobile_app_my_phone`. The alert opens the payment page when tapped.
- **Sessions for each person:** one page per member the login can book for, including next-of-kin profiles. List session types most preferred first.

## Booking rules

- Each person gets at most one session per day. The first type in their list with a free place is booked.
- For example, `BOOK THIS FIRST if on WGCSRC ERSA members list!` then `MEMBERS` books the ERSA session, falling back to `MEMBERS` only when ERSA is full.
- A day is skipped for anyone who already has a session or waiting-list place on it.
- A session that was ever booked and has since gone was cancelled, so that day is never auto-booked again. The integration remembers every booking it has seen, across restarts.
- Payment is never taken: card bookings hold the place unpaid until you pay through the link.
- The integration never cancels a booking.
- Once everyone has a place, waiting-list place or cancellation for the next session day, checks pause until that day's sessions end. The club releases one week at a time, so nothing new can appear before then.

## Entities and events

- `switch.coacha_auto_book` turns booking on and off.
- `sensor.coacha_<name>_next_booking` and `sensor.coacha_<name>_unpaid_bookings` list each person's bookings with payment links.
- `sensor.coacha_last_auto_booking` shows the latest booking made.
- `sensor.coacha_next_check` shows when the calendar will next be checked.
- `coacha_session_available` fires the first time a wanted session opens for booking.
- `coacha_booked` fires for every booking, waiting-list place or failed attempt, with `outcome` set to `booked`, `waiting_list` or `failed` and a `payment_url`.

## Development

```sh
uv sync
uv run pytest
uvx ruff check custom_components tests
```

[`docs/coacha-api.openapi.yaml`](docs/coacha-api.openapi.yaml) describes the Coacha API this is built on. Coacha does not publish it, so it may change without notice.
