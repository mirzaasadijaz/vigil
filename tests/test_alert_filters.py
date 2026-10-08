"""
Alerts page: date range, event-type filter and pagination.

The page used to fetch 300 rows every five seconds and filter them in the browser, which
meant anything older than those 300 was unreachable and the cost grew with the table.
These guard the properties that make the SQL version trustworthy:

  * the list and the count apply IDENTICAL filters - if they drift, the pager reports a
    page count for one query while listing rows from another, which reads as missing
    alerts and is very hard to see
  * a date is a LOCAL day. Every timestamp on this dashboard renders in Asia/Karachi, so
    comparing a picked date against UTC shifts the window five hours and drops the
    evening off the end
  * `date_to` is inclusive, because "from the 1st to the 1st" means that whole day
  * the response stays a bare ARRAY - six other callers depend on that shape
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.routers.alerts import TZ, _day_bounds  # noqa: E402


class TestLocalDayBounds:
    def test_a_single_date_spans_a_whole_local_day(self):
        lo, hi = _day_bounds("2026-09-01", "2026-09-01")
        assert hi - lo == timedelta(days=1), \
            "from X to X must mean the whole of X, not an empty range"
        assert lo.tzinfo is not None and hi.tzinfo is not None

    def test_the_day_is_local_not_utc(self):
        """The trap: Karachi is UTC+5, so a local day starts at 19:00 UTC the day before.
        Treating the picked date as UTC would silently drop the evening's alerts."""
        lo, _ = _day_bounds("2026-09-01", None)
        assert lo.astimezone(timezone.utc).hour == 19
        assert lo.astimezone(timezone.utc).day == 31        # 31 August, in UTC

    def test_a_late_evening_alert_is_inside_its_own_day(self):
        lo, hi = _day_bounds("2026-09-01", "2026-09-01")
        assert lo <= datetime(2026, 9, 1, 23, 30, tzinfo=TZ) < hi

    def test_just_after_midnight_belongs_to_the_next_day(self):
        lo, hi = _day_bounds("2026-09-01", "2026-09-01")
        assert not (lo <= datetime(2026, 9, 2, 0, 15, tzinfo=TZ) < hi)

    def test_no_dates_means_no_filtering(self):
        assert _day_bounds(None, None) == (None, None)

    def test_either_end_may_be_open(self):
        lo, hi = _day_bounds("2026-09-01", None)
        assert lo is not None and hi is None
        lo, hi = _day_bounds(None, "2026-09-01")
        assert lo is None and hi is not None


class TestFilterParity:
    def test_the_list_and_the_count_share_one_filter_builder(self):
        """Not two copies that must be kept in step by hand.

        If they diverge the pager describes a different query from the one it is paging,
        and the symptom - a page that says "51-100 of 300" but shows nothing - looks like
        data loss rather than a filter bug."""
        import inspect

        from backend.routers import alerts
        list_src = inspect.getsource(alerts.get_alerts)
        count_src = inspect.getsource(alerts.count_alerts)
        assert "_filtered(" in list_src and "_filtered(" in count_src

    def test_every_filter_is_accepted_by_both_endpoints(self):
        import inspect

        from backend.routers import alerts
        wanted = {"module", "camera_id", "employee_id", "event_type",
                  "severity", "feedback", "date_from", "date_to"}
        for fn in (alerts.get_alerts, alerts.count_alerts):
            params = set(inspect.signature(fn).parameters)
            assert wanted <= params, f"{fn.__name__} is missing {wanted - params}"


class TestPaging:
    def test_the_page_size_is_bounded(self):
        """A typo in the URL must not ask for the whole table - the response is built
        row by row in Python, so an unbounded limit is a denial of service on ourselves."""
        import inspect

        from backend.routers import alerts
        src = inspect.getsource(alerts.get_alerts)
        assert "min(int(limit), 1000)" in src

    def test_offset_cannot_go_negative(self):
        import inspect

        from backend.routers import alerts
        assert "max(0, int(offset))" in inspect.getsource(alerts.get_alerts)

    def test_the_list_still_returns_a_bare_array(self):
        """Six callers depend on this: the toaster, the sidebar, the alert feed,
        attendance, the employee detail panel and the alerts page itself. The total goes
        in a separate /count endpoint precisely so this shape survives."""
        import inspect

        from backend.routers import alerts
        src = inspect.getsource(alerts.get_alerts)
        assert "return data" in src
        assert "return {" not in src.split("return data")[0].split("data = []")[-1]
