"""Drive the real aiogram handlers with a fake Telegram session (no network)."""
import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import Chat, Message, Update, User  # noqa: E402

import bot as kpi_bot  # noqa: E402


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(message_id=len(self.sent), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass


DP = Dispatcher()
DP.include_router(kpi_bot.router)


def run(text, chat_id=111, allowed=frozenset()):
    kpi_bot.cfg.allowed = set(allowed)
    session = FakeSession()
    bot = Bot("123456:TEST", session=session)
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                  from_user=User(id=chat_id, is_bot=False, first_name="T"), text=text)

    async def go():
        await DP.feed_update(bot, Update(update_id=1, message=msg))

    asyncio.run(go())
    return [m.text for m in session.sent]


class Handlers(unittest.TestCase):
    def test_day_with_incident(self):
        out = run("/day 2026-11-18")
        self.assertIn("FTDs collapsed", out[0])

    def test_bad_date_explains_format(self):
        self.assertIn("Send a date like", run("/day yesterday")[0])

    def test_today_week_alerts(self):
        self.assertIn("Daily KPI", run("/today")[0])
        self.assertIn("Week", run("/week")[0])
        self.assertIn("Alerts", run("/alerts")[0])

    def test_allow_list_blocks_strangers_but_id_works(self):
        self.assertIn("private", run("/today", chat_id=5, allowed={111})[0])
        self.assertIn("5", run("/id", chat_id=5, allowed={111})[0])

    def tearDown(self):
        kpi_bot.cfg.allowed = set()


if __name__ == "__main__":
    unittest.main()
