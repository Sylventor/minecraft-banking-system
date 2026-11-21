from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.business_calendar import analytics_period, next_occurrence, wall_time
from app.business_csv import safe_csv


def test_dst_gap_and_ambiguous_time_have_one_defined_occurrence():
    zone = ZoneInfo("Europe/Prague")
    assert wall_time(datetime(2026, 3, 29, 2, 30), zone) == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert wall_time(datetime(2026, 10, 25, 2, 30), zone) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_monthly_clamps_last_day_and_biweekly_keeps_anchor():
    rule = {"timezone": "Europe/Prague", "local_time": "10:00", "frequency": "monthly", "month_day": 31, "weekday": 1}
    assert next_occurrence(rule, datetime(2026, 2, 1, tzinfo=UTC)) == datetime(2026, 2, 28, 9, tzinfo=UTC)
    rule["frequency"] = "biweekly"
    anchor = datetime(2026, 3, 16, 9, tzinfo=UTC)
    assert next_occurrence(rule, datetime(2026, 3, 28, tzinfo=UTC), anchor) == datetime(2026, 3, 30, 8, tzinfo=UTC)


def test_periods_are_half_open_and_local_midnight_respects_dst():
    period = analytics_period("today", "Europe/Prague", None, None, datetime(2026, 3, 29, 12, tzinfo=UTC))
    start, end = datetime.fromisoformat(period["from"]), datetime.fromisoformat(period["to"])
    assert (end - start).total_seconds() == 23 * 3600
    month = analytics_period("previous_month", "UTC", None, None, datetime(2026, 3, 15, tzinfo=UTC))
    assert month["from"].startswith("2026-02-01") and month["to"].startswith("2026-03-01")


def test_csv_formula_cells_are_never_executable():
    csv = safe_csv(["name"], [["=1+1"], ["  @SUM(1)"], ["-10.00"], ["normal"]]).body.decode()
    assert "'=1+1" in csv and "'  @SUM(1)" in csv and "'-10.00" in csv and "normal" in csv
