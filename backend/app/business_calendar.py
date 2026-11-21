"""One calendar policy for analytics and a dedicated payroll worker (IANA zones)."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def wall_time(local: datetime, zone: ZoneInfo) -> datetime:
    # Ambiguous times run once at the first occurrence. A DST gap moves to the
    # first valid minute, rather than silently changing the organization's zone.
    for minute in range(181):
        candidate = local + timedelta(minutes=minute)
        value = candidate.replace(tzinfo=zone, fold=0)
        if value.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == candidate:
            return value.astimezone(UTC)
    raise ValueError("No valid local time within three hours")


def next_occurrence(rule: dict[str, Any], after: datetime, anchor: datetime | None = None) -> datetime:
    zone = ZoneInfo(rule["timezone"])
    clock = time.fromisoformat(rule["local_time"])
    local = after.astimezone(zone)
    frequency = rule["frequency"]
    date = anchor.astimezone(zone).date() if anchor else local.date()
    if frequency != "monthly" and anchor is None:
        date += timedelta(days=(int(rule["weekday"]) - date.isoweekday()) % 7)
    for _ in range(2048):
        if frequency == "monthly":
            date = date.replace(day=min(int(rule["month_day"]), calendar.monthrange(date.year, date.month)[1]))
        candidate = wall_time(datetime.combine(date, clock), zone)
        if candidate > after:
            return candidate
        if frequency == "monthly":
            year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
            date = date.replace(year=year, month=month, day=1)
        else:
            date += timedelta(days=14 if frequency == "biweekly" else 7)
    raise ValueError("Schedule exceeds supported catch-up window")


def analytics_period(
    period: str, timezone: str, start: datetime | None, end: datetime | None, now: datetime | None = None
) -> dict[str, str]:
    zone = ZoneInfo(timezone)
    local = (now or datetime.now(UTC)).astimezone(zone)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if start is not None or end is not None:
        if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Both custom boundaries require timezones")
        previous_end = start
        previous_start = start - (end - start)
    else:
        end = today + timedelta(days=1)
        if period in {"today", "7d", "30d", "90d"}:
            days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}[period]
            start = end - timedelta(days=days)
            previous_end, previous_start = start, start - timedelta(days=days)
        else:
            month = today.replace(day=1)
            prior = (month - timedelta(days=1)).replace(day=1)
            if period == "month":
                start, end = month, (month.replace(day=28) + timedelta(days=4)).replace(day=1)
                previous_start, previous_end = prior, month
            elif period == "previous_month":
                start, end = prior, month
                previous_start, previous_end = (prior - timedelta(days=1)).replace(day=1), prior
            else:
                raise ValueError("Unknown period")
    assert start is not None and end is not None
    duration = end.astimezone(UTC) - start.astimezone(UTC)
    if duration <= timedelta(0) or duration > timedelta(days=370):
        raise ValueError("Period must be positive and at most 370 days")
    return {
        "from": start.astimezone(UTC).isoformat(),
        "to": end.astimezone(UTC).isoformat(),
        "previous_from": previous_start.astimezone(UTC).isoformat(),
        "previous_to": previous_end.astimezone(UTC).isoformat(),
        "timezone": timezone,
    }
