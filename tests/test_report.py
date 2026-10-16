import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report  # noqa: E402
from bot import seconds_until  # noqa: E402
from make_demo_data import INCIDENTS  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "demo_data" / "daily.csv"


class Alerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily = report.load_daily(DEMO)
        dates = report.available_dates(cls.daily)
        cls.all_alerts = report.alerts_between(cls.daily, dates[0], dates[-1])

    def alerts_on(self, day, channel):
        return [a for a in self.all_alerts if a.day == date.fromisoformat(day) and a.channel == channel]

    def test_every_injected_incident_is_caught(self):
        for day, channel, _, _ in INCIDENTS:
            with self.subTest(day=day, channel=channel):
                self.assertTrue(self.alerts_on(day, channel))

    def test_broken_postback_is_critical_and_reported_once(self):
        found = self.alerts_on("2026-11-18", "Affiliates")
        self.assertEqual([a.severity for a in found], [report.CRITICAL])

    def test_clean_history_is_quiet(self):
        incident_keys = {(date.fromisoformat(d), c) for d, c, _, _ in INCIDENTS}
        false_alarms = [a for a in self.all_alerts if (a.day, a.channel) not in incident_keys]
        # one genuine outlier exists in the simulated history (26 Oct, in-stream conversion halved)
        self.assertLessEqual(len(false_alarms), 1)

    def test_affiliate_spend_never_flags(self):
        self.assertFalse([a for a in self.all_alerts if a.channel == "Affiliates" and a.title == "Spend above normal"])


class Dates(unittest.TestCase):
    def test_report_date_falls_back_to_latest(self):
        daily = report.load_daily(DEMO)
        self.assertEqual(report.pick_report_date(daily, date(2030, 1, 1)), report.available_dates(daily)[-1])
        self.assertEqual(report.pick_report_date(daily, date(2026, 11, 19)), date(2026, 11, 18))

    def test_next_digest_is_tomorrow_when_time_has_passed(self):
        tz = ZoneInfo("Europe/London")
        now = datetime(2026, 11, 18, 10, 0, tzinfo=tz)
        self.assertEqual(seconds_until(time(9, 0), tz, now), 23 * 3600)
        self.assertEqual(seconds_until(time(11, 0), tz, now), 3600)


class Formatting(unittest.TestCase):
    def test_digest_escapes_html(self):
        daily = report.load_daily(DEMO)
        daily.loc[0, "channel"] = "A<b>&"
        text = report.daily_digest(daily, daily.loc[0, "date"])
        self.assertIn("A&lt;b&gt;&amp;", text)


if __name__ == "__main__":
    unittest.main()
