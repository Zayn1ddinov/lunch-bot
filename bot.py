import asyncio
import logging
import re

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, ReactionTypeEmoji

import admin
from core import (GROUP_CHAT_ID, bot, db, is_admin, refresh_poll, schedule_jobs, scheduler,
                  setting, stats_text, this_month, today)

# «+», «+2», «+ 3» — записаться (число = порций, например с гостем); «-» — отменить
PLUS_RE = re.compile(r"^\+\s*(\d{1,2})?$")
MINUS_RE = re.compile(r"^[-−–]\s*1?$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

dp = Dispatcher()
dp.include_router(admin.router)


# ---------- «+» и «-» в группе ----------

@dp.message(F.chat.id == GROUP_CHAT_ID, F.text.regexp(PLUS_RE) | F.text.regexp(MINUS_RE))
async def on_plus_minus(message: Message):
    day = today()
    user = message.from_user
    if not db.is_open(day):
        if db.get_day(day):
            await message.reply("🔒 Приём заявок на сегодня уже закрыт.")
        return  # до начала опроса «+» молча игнорируем
    db.save_user(user.id, user.full_name, user.username)
    m = PLUS_RE.match(message.text.strip())
    portions = int(m.group(1) or 1) if m else 0
    portions = max(0, min(portions, 20))
    db.set_order(day, user.id, portions)
    try:
        await message.react([ReactionTypeEmoji(emoji="👍" if portions else "👌")])
    except TelegramBadRequest:
        pass
    await refresh_poll(day)


# ---------- команды ----------

@dp.message(Command("chatid"))
async def cmd_chatid(message: Message):
    await message.answer(f"chat_id: <code>{message.chat.id}</code>\nваш user_id: <code>{message.from_user.id}</code>",
                         parse_mode="HTML")


@dp.message(Command("help", "start"))
async def cmd_help(message: Message):
    text = (f"Каждый рабочий день в {setting('poll_time')} я пишу в группу, кто будет обедать.\n"
            f"Ставьте <b>+</b> (с гостем <b>+2</b>, отменить <b>-</b>) до {setting('report_time')} — "
            "потом отправляю заявку на кухню.\n\n/stats — кто сколько раз обедал в этом месяце")
    if await is_admin(message.from_user.id):
        text += "\n\n⚙️ Админам: напишите мне в личку /admin — панель настроек и отчёты."
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("admin"), F.chat.type != "private")
async def cmd_admin_in_group(message: Message):
    me = await bot.get_me()
    await message.reply(f"Панель настроек открывается в личке: @{me.username} → /admin")


@dp.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject):
    await message.answer(stats_text(*this_month()))


async def main():
    schedule_jobs()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
