"""KPI digest and anomaly alerts for paid player acquisition.

Everything here is plain pandas and has no Telegram dependency, so it can be
tested and previewed without a bot token.

Input: a daily table with columns  date, channel, spend, registrations, ftds
(one row per channel per active day; a channel with no spend that day has no row).

Each channel's day is compared with its own recent baseline: the previous 7
active days, or the previous 4 runs for channels bought in bursts (Masthead).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd

CHANNEL_ORDER = ["Google Search", "YouTube in-stream", "YouTube Masthead", "Affiliates"]
BURST_CHANNELS = {"YouTube Masthead"}          # bought as scheduled one-day placements
CPA_PRICED_CHANNELS = {"Affiliates"}           # paid per FTD, so spend simply follows results
BASELINE_ROWS = {"default": 7, "burst": 4}

# Thresholds were tuned on 12 weeks of clean history so that normal day-to-day
# noise raises no alerts; see tests/test_report.py::test_clean_history_is_quiet.
TRACKING_FTD_RATIO = 0.30      # FTDs fell below 30% of normal ...
TRACKING_REG_RATIO = 0.60      # ... while sign-ups stayed above 60% of normal
CPA_SPIKE_RATIO = 1.60
CONVERSION_DROP_RATIO = 0.55
SPEND_SPIKE_RATIO = 1.40
MIN_REGS_FOR_CONVERSION = 30

CRITICAL, WARNING = "critical", "warning"


@dataclass(frozen=True)
class Alert:
    day: date
    channel: str
    severity: str
    title: str
    detail: str


def load_daily(path: Path | str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df.sort_values(["date", "channel"]).reset_index(drop=True)


def available_dates(daily: pd.DataFrame) -> list[date]:
    return sorted(daily["date"].unique())


def pick_report_date(daily: pd.DataFrame, today: date) -> date:
    """Yesterday if the data has it, otherwise the latest day in the data
    (the demo dataset is historical)."""
    dates = available_dates(daily)
    yesterday = date.fromordinal(today.toordinal() - 1)
    return yesterday if yesterday in dates else dates[-1]


def _baseline(daily: pd.DataFrame, channel: str, day: date) -> pd.DataFrame:
    n = BASELINE_ROWS["burst" if channel in BURST_CHANNELS else "default"]
    hist = daily[(daily["channel"] == channel) & (daily["date"] < day)]
    return hist.tail(n)


def channel_day(daily: pd.DataFrame, day: date) -> pd.DataFrame:
    """One row per channel active on `day`, with its baseline averages."""
    rows = []
    for _, r in daily[daily["date"] == day].iterrows():
        base = _baseline(daily, r["channel"], day)
        b_spend = base["spend"].mean() if len(base) else None
        b_regs = base["registrations"].mean() if len(base) else None
        b_ftds = base["ftds"].mean() if len(base) else None
        b_cpa = base["spend"].sum() / base["ftds"].sum() if len(base) and base["ftds"].sum() else None
        b_conv = base["ftds"].sum() / base["registrations"].sum() if len(base) and base["registrations"].sum() else None
        rows.append(dict(
            channel=r["channel"], spend=r["spend"], registrations=int(r["registrations"]), ftds=int(r["ftds"]),
            cpa=r["spend"] / r["ftds"] if r["ftds"] else None,
            conv=r["ftds"] / r["registrations"] if r["registrations"] else None,
            base_days=len(base), base_spend=b_spend, base_regs=b_regs, base_ftds=b_ftds,
            base_cpa=b_cpa, base_conv=b_conv,
        ))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    order = {c: i for i, c in enumerate(CHANNEL_ORDER)}
    return out.sort_values("channel", key=lambda s: s.map(order).fillna(99)).reset_index(drop=True)


def detect_alerts(daily: pd.DataFrame, day: date) -> list[Alert]:
    alerts: list[Alert] = []
    for r in channel_day(daily, day).itertuples():
        if r.base_days < 3:
            continue
        ch = r.channel

        # 1. Tracking: sign-ups look normal but deposits vanished -> postback / deposit tracking is likely broken.
        if r.ftds < TRACKING_FTD_RATIO * r.base_ftds and r.registrations >= TRACKING_REG_RATIO * r.base_regs:
            alerts.append(Alert(day, ch, CRITICAL, "FTDs collapsed, sign-ups normal",
                f"{r.ftds} FTDs vs ~{r.base_ftds:.0f} usual, while sign-ups are {r.registrations} vs ~{r.base_regs:.0f}. "
                "Check the deposit postback / tracking before changing bids."))
            continue  # the other checks would only repeat this problem

        conversion_drop = (r.registrations >= MIN_REGS_FOR_CONVERSION and r.conv is not None and r.base_conv
                           and r.conv < CONVERSION_DROP_RATIO * r.base_conv)
        cpa_spike = r.cpa is not None and r.base_cpa and r.cpa > CPA_SPIKE_RATIO * r.base_cpa
        if conversion_drop:
            cpa_note = f" CPA £{r.cpa:,.0f} vs £{r.base_cpa:,.0f} usual." if r.cpa and r.base_cpa else ""
            alerts.append(Alert(day, ch, WARNING, "Sign-up → FTD conversion drop",
                f"{r.conv:.0%} of sign-ups deposited vs {r.base_conv:.0%} usual.{cpa_note} "
                "Check the deposit flow and payment methods."))
        elif cpa_spike:
            alerts.append(Alert(day, ch, WARNING, "CPA spike",
                f"CPA £{r.cpa:,.0f} vs £{r.base_cpa:,.0f} usual ({r.cpa / r.base_cpa - 1:+.0%}). "
                "Check placements and creatives."))

        if (ch not in BURST_CHANNELS and ch not in CPA_PRICED_CHANNELS
                and r.spend > SPEND_SPIKE_RATIO * r.base_spend):
            alerts.append(Alert(day, ch, WARNING, "Spend above normal",
                f"£{r.spend:,.0f} spent vs ~£{r.base_spend:,.0f} usual ({r.spend / r.base_spend - 1:+.0%}). "
                "Check budgets and bid changes."))
    return alerts


def alerts_between(daily: pd.DataFrame, start: date, end: date) -> list[Alert]:
    found = []
    for d in available_dates(daily):
        if start <= d <= end:
            found.extend(detect_alerts(daily, d))
    return found


# ---------- formatting (Telegram HTML) ----------

def _money(x: float) -> str:
    return f"£{x / 1000:,.1f}k" if x >= 10_000 else f"£{x:,.0f}"


def _delta(now: float | None, base: float | None) -> str:
    if now is None or not base:
        return ""
    return f" ({now / base - 1:+.0%})"


ICON = {CRITICAL: "🔴", WARNING: "🟠"}


def format_alert(a: Alert, with_date: bool = False) -> str:
    when = f"{a.day:%d %b} · " if with_date else ""
    return f"{ICON[a.severity]} <b>{escape(when + a.channel)}: {escape(a.title)}</b>\n{escape(a.detail)}"


def daily_digest(daily: pd.DataFrame, day: date) -> str:
    cd = channel_day(daily, day)
    if cd.empty:
        return f"No data for {day:%d %b %Y}."
    spend, regs, ftds = cd["spend"].sum(), int(cd["registrations"].sum()), int(cd["ftds"].sum())

    lines = [f"<b>Daily KPI · {day:%a %d %b %Y}</b>", ""]
    lines.append(f"Spend <b>{_money(spend)}</b> · Sign-ups <b>{regs:,}</b> · FTDs <b>{ftds:,}</b>")
    lines.append(f"Blended CPA <b>£{spend / ftds:,.0f}</b>" if ftds else "Blended CPA —")
    lines.append("")
    for r in cd.itertuples():
        cpa = f"CPA £{r.cpa:,.0f}{_delta(r.cpa, r.base_cpa)}" if r.cpa else "CPA —"
        lines.append(f"• <b>{escape(r.channel)}</b>: {_money(r.spend)} · {r.ftds} FTDs · {cpa}")
    if any(c in BURST_CHANNELS for c in cd["channel"]):
        lines.append("<i>Masthead ran today; expect a lift in brand search today and tomorrow.</i>")

    alerts = detect_alerts(daily, day)
    lines.append("")
    if alerts:
        lines.append(f"<b>Alerts ({len(alerts)})</b>")
        lines.append("\n\n".join(format_alert(a) for a in alerts))
    else:
        lines.append("✅ No anomalies against each channel's recent baseline.")
    lines.append("")
    lines.append("<i>Changes in brackets are vs the channel's previous 7 active days (Masthead: previous 4 runs).</i>")
    return "\n".join(lines)


def weekly_summary(daily: pd.DataFrame, end: date) -> str:
    dates = [d for d in available_dates(daily) if d <= end]
    this_week, prev_week = dates[-7:], dates[-14:-7]
    if len(this_week) < 7:
        return "Not enough data for a weekly summary yet."

    def agg(ds):
        part = daily[daily["date"].isin(ds)].groupby("channel")[["spend", "registrations", "ftds"]].sum()
        return part.reindex([c for c in CHANNEL_ORDER if c in part.index])

    now, prev = agg(this_week), agg(prev_week) if len(prev_week) == 7 else None
    lines = [f"<b>Week {this_week[0]:%d %b} – {this_week[-1]:%d %b}</b>", ""]
    for ch, r in now.iterrows():
        cpa = r.spend / r.ftds if r.ftds else None
        p_cpa = None
        if prev is not None and ch in prev.index and prev.loc[ch, "ftds"]:
            p_cpa = prev.loc[ch, "spend"] / prev.loc[ch, "ftds"]
        cpa_txt = f"CPA £{cpa:,.0f}{_delta(cpa, p_cpa)}" if cpa else "CPA —"
        lines.append(f"• <b>{escape(ch)}</b>: {_money(r.spend)} · {int(r.ftds)} FTDs · {cpa_txt}")
    tot = now.sum()
    lines.append("")
    lines.append(f"Total {_money(tot.spend)} · {int(tot.ftds):,} FTDs · CPA £{tot.spend / tot.ftds:,.0f}")
    n_alerts = len(alerts_between(daily, this_week[0], this_week[-1]))
    lines.append(f"Alerts this week: {n_alerts}" + (" (send /alerts for details)" if n_alerts else ""))
    if prev is not None:
        lines.append("<i>CPA change is vs the previous 7 days.</i>")
    return "\n".join(lines)


def recent_alerts(daily: pd.DataFrame, end: date, days: int = 14) -> str:
    dates = [d for d in available_dates(daily) if d <= end][-days:]
    found = alerts_between(daily, dates[0], dates[-1])
    if not found:
        return f"No alerts in the {days} days to {end:%d %b}."
    head = f"<b>Alerts, {dates[0]:%d %b} – {dates[-1]:%d %b}</b>"
    return "\n\n".join([head] + [format_alert(a, with_date=True) for a in found])
