"""Regression tests for weekday lettering, cycle boundaries, and validation."""

from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError, validate_call

from catalog_app.tools.dinner_ideas import MENUS, dinner_ideas


class DinnerIdeasTests(unittest.TestCase):
    def test_example_week(self):
        result = dinner_ideas(for_date="2026-10-26", days=7)
        self.assertEqual([row["dinner"] for row in result["dinners"]], [
            "Meatloaf", "Tacos", "Wok-fried chicken", "Tikka masala",
            "Fried fish", "Sloppy joes", "Sofrito chicken",
        ])

    def test_90_unique_dinners_for_every_start_weekday(self):
        for shift in range(7):
            start = (date(2026, 10, 26) + timedelta(days=shift)).isoformat()
            rows = dinner_ideas(for_date=start, cycle_start=start, days=90)["dinners"]
            self.assertEqual(len(rows), 90)
            self.assertEqual(len({row["dinner"] for row in rows}), 90)
            self.assertEqual([row["cycle_day"] for row in rows], list(range(1, 91)))
            for row in rows:
                self.assertEqual(row["dinner"][0], row["letter"])
                self.assertEqual(row["letter"], date.fromisoformat(row["date"]).strftime("%A")[0])

    def test_menu_inventory(self):
        self.assertEqual([len(menu) for menu in MENUS], [13] * 7)
        self.assertEqual(len({name for menu in MENUS for name, _ in menu}), 91)

    def test_day_90_rolls_over_on_real_weekday(self):
        rows = dinner_ideas(for_date="2027-01-23", days=3)["dinners"]
        self.assertEqual([row["cycle_day"] for row in rows], [90, 1, 2])
        self.assertEqual([row["cycle_number"] for row in rows], [1, 2, 2])
        self.assertEqual([row["weekday"] for row in rows], ["Saturday", "Sunday", "Monday"])
        self.assertEqual(rows[1]["dinner"], "Sofrito chicken")
        self.assertEqual(rows[2]["dinner"], "Meatloaf")

    def test_single_day_matches_range(self):
        rows = dinner_ideas(for_date="2026-10-26", days=90)["dinners"]
        for row in rows:
            self.assertEqual(dinner_ideas(for_date=row["date"])["dinners"][0], row)

    def test_long_running_cycles_keep_weekday_letters(self):
        for offset in range(0, 900, 90):
            requested = (date(2026, 10, 26) + timedelta(days=offset)).isoformat()
            for row in dinner_ideas(for_date=requested, days=90)["dinners"]:
                self.assertEqual(row["letter"], row["dinner"][0])

    def test_today_uses_requested_timezone(self):
        instant = datetime(2026, 10, 29, 2, 30, tzinfo=ZoneInfo("UTC"))
        with patch("catalog_app.tools.dinner_ideas.datetime") as clock:
            clock.now.side_effect = lambda zone: instant.astimezone(zone)
            self.assertEqual(dinner_ideas()["dinners"][0]["date"], "2026-10-28")
            self.assertEqual(dinner_ideas(time_zone="Asia/Tokyo")["dinners"][0]["date"], "2026-10-29")

    def test_invalid_dates_and_timezones(self):
        for kwargs in [
            {"for_date": "2026-02-30"}, {"for_date": "20261028"},
            {"for_date": "tomorrow"}, {"cycle_start": ""},
            {"for_date": "2026-10-25"}, {"time_zone": "Not/AZone"},
            {"time_zone": ""}, {"time_zone": "/etc/passwd"},
            {"for_date": "9999-12-31", "days": 2},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                dinner_ideas(**kwargs)

    def test_invalid_day_counts(self):
        for days in [0, 91, -1, True, 1.5, "7"]:
            with self.subTest(days=days), self.assertRaises(ValueError):
                dinner_ideas(days=days)

    def test_input_schema_rejects_non_integer_days(self):
        checked = validate_call(dinner_ideas)
        for days in [True, 1.5, "7", 91, 0]:
            with self.subTest(days=days), self.assertRaises(ValidationError):
                checked(days=days)


if __name__ == "__main__":
    unittest.main()
