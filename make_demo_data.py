"""Build demo_data/daily.csv: the simulated history from the companion
dashboard project (github.com/nkd077/igaming-marketing-dashboard) with three
incidents added on purpose, so the bot has something to catch.

    python make_demo_data.py --source ../igaming-marketing-dashboard/data/daily.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# date, channel, changes, what happened
INCIDENTS = [
    ("2026-11-18", "Affiliates", dict(ftds=3), "deposit postback broken: sign-ups arrive, FTDs don't"),
    ("2026-12-03", "YouTube in-stream", dict(registrations=30, ftds=8), "poor placement: traffic quality drops"),
    ("2026-12-10", "Google Search", dict(spend=10900.0, registrations=92, ftds=41), "bid strategy misconfigured"),
]
AFFILIATE_CPA = 190


def apply_incidents(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    for day, channel, changes, _ in INCIDENTS:
        mask = (daily["date"] == day) & (daily["channel"] == channel)
        if not mask.any():
            raise ValueError(f"No row for {channel} on {day}")
        for col, value in changes.items():
            daily.loc[mask, col] = value
        if channel == "Affiliates":  # paid per FTD
            daily.loc[mask, "spend"] = daily.loc[mask, "ftds"] * AFFILIATE_CPA
    return daily


def main() -> None:
    root = Path(__file__).parent
    parser = argparse.ArgumentParser(description="Add demo incidents to simulated daily data")
    parser.add_argument("--source", type=Path, default=root.parent / "igaming-marketing-dashboard" / "data" / "daily.csv")
    parser.add_argument("--out", type=Path, default=root / "demo_data" / "daily.csv")
    args = parser.parse_args()

    daily = apply_incidents(pd.read_csv(args.source))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.out, index=False)
    print(f"Wrote {args.out} with {len(INCIDENTS)} incidents:")
    for day, channel, _, what in INCIDENTS:
        print(f"  {day}  {channel}: {what}")


if __name__ == "__main__":
    main()
