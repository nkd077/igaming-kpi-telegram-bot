"""Telegram bot that posts a daily KPI digest and anomaly alerts for a paid
acquisition team.

Run:
    cp .env.example .env    # add BOT_TOKEN from @BotFather
    python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, Message

import report

log = logging.getLogger("kpi-bot")


def load_env(path: Path) -> None:
    """Minimal .env reader so the bot has no extra dependency for it."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


class Config:
    def __init__(self) -> None:
        root = Path(__file__).parent
        load_env(root / ".env")
        self.token = os.environ.get("BOT_TOKEN", "")
        self.data_path = Path(os.environ.get("DATA_PATH", root / "demo_data" / "daily.csv"))
        self.allowed = parse_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))
        self.digest_chats = parse_ids(os.environ.get("DIGEST_CHAT_IDS", "")) or self.allowed
        self.tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/London"))
        hh, mm = os.environ.get("DIGEST_TIME", "09:00").split(":")
        self.digest_time = time(int(hh), int(mm))


cfg = Config()
router = Router()

HELP = (
    "<b>KPI bot</b>\n"
    "/today — digest for the latest day\n"
    "/day YYYY-MM-DD — digest for any day\n"
    "/week — last 7 days by channel\n"
    "/alerts — alerts from the last 60 days\n"
    "/id — show this chat's ID (for the allow-list)\n\n"
    "A digest is also posted automatically every morning."
)


def allowed(message: Message) -> bool:
    return not cfg.allowed or message.chat.id in cfg.allowed


def data():
    # Re-read on every request so a nightly export is picked up without a restart.
    return report.load_daily(cfg.data_path)


def today_local() -> date:
    return datetime.now(cfg.tz).date()


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Chat ID: <code>{message.chat.id}</code>")


@router.message(~F.func(allowed))
async def not_allowed(message: Message) -> None:
    await message.answer("This bot is private. Ask the admin to add this chat ID: "
                         f"<code>{message.chat.id}</code>")


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    daily = data()
    await message.answer(report.daily_digest(daily, report.pick_report_date(daily, today_local())))


@router.message(Command("day"))
async def cmd_day(message: Message, command: CommandObject) -> None:
    daily = data()
    dates = report.available_dates(daily)
    try:
        day = date.fromisoformat((command.args or "").strip())
    except ValueError:
        await message.answer(f"Send a date like <code>/day {dates[-1]:%Y-%m-%d}</code>. "
                             f"Data covers {dates[0]:%d %b %Y} – {dates[-1]:%d %b %Y}.")
        return
    await message.answer(report.daily_digest(daily, day))


@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    daily = data()
    await message.answer(report.weekly_summary(daily, report.pick_report_date(daily, today_local())))


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    daily = data()
    await message.answer(report.recent_alerts(daily, report.pick_report_date(daily, today_local()), days=60))


def seconds_until(target: time, tz: ZoneInfo, now: datetime | None = None) -> float:
    now = now or datetime.now(tz)
    run = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return (run - now).total_seconds()


async def digest_loop(bot: Bot) -> None:
    if not cfg.digest_chats:
        log.warning("DIGEST_CHAT_IDS / ALLOWED_CHAT_IDS empty: scheduled digest is off")
        return
    while True:
        wait = seconds_until(cfg.digest_time, cfg.tz)
        log.info("Next digest in %.0f min", wait / 60)
        await asyncio.sleep(wait)
        try:
            daily = data()
            text = report.daily_digest(daily, report.pick_report_date(daily, today_local()))
        except Exception:
            log.exception("Could not build the digest")
            continue
        for chat_id in cfg.digest_chats:
            try:
                await bot.send_message(chat_id, text)
            except Exception:
                log.exception("Could not send digest to %s", chat_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not cfg.token:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and add the token from @BotFather.")
    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="today", description="Digest for the latest day"),
        BotCommand(command="week", description="Last 7 days by channel"),
        BotCommand(command="alerts", description="Alerts from the last 60 days"),
        BotCommand(command="day", description="Digest for a date: /day YYYY-MM-DD"),
        BotCommand(command="help", description="What this bot does"),
    ])
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(digest_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
