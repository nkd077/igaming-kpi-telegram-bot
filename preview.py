"""Print what the bot would send, without Telegram or a token.

    python preview.py                     # digest for the latest day
    python preview.py --day 2026-11-18    # a day with a critical alert
    python preview.py --week
    python preview.py --alerts
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from html import unescape
from pathlib import Path

import report


def plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview bot messages in the terminal")
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "demo_data" / "daily.csv")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--day", type=date.fromisoformat)
    group.add_argument("--week", action="store_true")
    group.add_argument("--alerts", action="store_true")
    args = parser.parse_args()

    daily = report.load_daily(args.data)
    latest = report.available_dates(daily)[-1]
    if args.week:
        text = report.weekly_summary(daily, latest)
    elif args.alerts:
        text = report.recent_alerts(daily, latest, days=90)
    else:
        text = report.daily_digest(daily, args.day or latest)
    print(plain(text))


if __name__ == "__main__":
    main()
