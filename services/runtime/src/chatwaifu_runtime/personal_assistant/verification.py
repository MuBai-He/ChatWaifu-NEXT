"""Provider-state verification: model narration is never a write receipt."""

from datetime import date, datetime
from typing import cast

from chatwaifu_runtime.personal_assistant.google_calendar import CalendarEvent, GoogleCalendarError


def matching_provider_etag(current: str | None, supplied: str) -> str | None:
    """Recognize the same opaque version, including bounded JSON quote over-escaping."""
    if current is not None:
        if supplied == current:
            return current
        if current.startswith('"') and current.endswith('"'):
            for depth in (1, 2, 3):
                if supplied == current.replace('"', "\\" * depth + '"'):
                    return current
    return None


def verify_calendar(
    event: CalendarEvent,
    identifier: str,
    changes: dict[str, object],
    before: CalendarEvent | None = None,
) -> None:
    if event.id != identifier or event.status == "cancelled":
        raise GoogleCalendarError("calendar_verification_failed", retryable=True)
    if "summary" in changes and event.title != changes["summary"]:
        raise GoogleCalendarError("calendar_verification_failed", retryable=True)
    for field, actual in (("location", event.location), ("description", event.description)):
        if field in changes and actual != changes[field]:
            raise GoogleCalendarError("calendar_verification_failed", retryable=True)
    for name, value in (("start", event.start), ("end", event.end)):
        change = changes.get(name)
        if isinstance(change, dict) and isinstance(
            cast(dict[str, object], change).get("dateTime"), str
        ):
            expected = datetime.fromisoformat(cast(str, change["dateTime"]))
            if value is None or value.timestamp != expected:
                raise GoogleCalendarError("calendar_verification_failed", retryable=True)
        if isinstance(change, dict) and isinstance(
            cast(dict[str, object], change).get("date"), str
        ):
            expected_day = date.fromisoformat(cast(str, change["date"]))
            if value is None or value.day != expected_day or value.timestamp is not None:
                raise GoogleCalendarError("calendar_verification_failed", retryable=True)
    if before and (
        before.recurring_event_id != event.recurring_event_id
        or before.original_start != event.original_start
    ):
        raise GoogleCalendarError("calendar_instance_identity_changed", retryable=True)
