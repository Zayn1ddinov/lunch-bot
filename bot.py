import asyncio
import logging
import os
import re
from logging.handlers import RotatingFileHandler

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, Message, PollAnswer, ReactionTypeEmoji

import admin
import user
from core import (GROUP_CHAT_ID, POLL_PORTIONS, bot, bot_username, db, refresh_poll, schedule_jobs,
                  scheduler, setting, stats_text, this_month, today)

# «+», «+2», «+ 3» — записаться (число = порций, например с гостем); «-» — отменить
PLUS_RE = re.compile(r"^\+\s*(\d{1,2})?$")
MINUS_RE = re.compile(r"^[-−–]\s*1?$")

# лог в консоль и в файл LOG_DIR/bot.log (5 файлов по 5 МБ); LOG_DIR= пустой — только консоль (Docker)
LOG_DIR = os.getenv("LOG_DIR", "logs")
handlers = [logging.StreamHandler()]
if LOG_DIR:
    os.makedirs(LOG_DIR, exist_ok=True)
    handlers.append(RotatingFileHandler(os.path.join(LOG_DIR, "bot.log"), maxBytes=5_000_000, backupCount=5,
                                        encoding="utf-8"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

dp = Dispatcher()
# личка сотрудников (профиль, оплата) и панель администратора
dp.include_routers(user.router, admin.router)


# ---------- голосование в группе ----------

@dp.message(F.chat.id == GROUP_CHAT_ID, F.text.regexp(PLUS_RE) | F.text.regexp(MINUS_RE))
async def on_plus_minus(message: Message):
    day = today()
    u = message.from_user
    if not db.is_open(day):
        if db.get_day(day):
            await message.reply("🔒 Приём заявок на сегодня уже закрыт.")
        return  # до начала опроса «+» молча игнорируем
    db.save_user(u.id, u.full_name, u.username)
    m = PLUS_RE.match(message.text.strip())
    portions = max(0, min(int(m.group(1) or 1) if m else 0, 20))
    db.set_order(day, u.id, portions)
    try:
        await message.react([ReactionTypeEmoji(emoji="👍" if portions else "👌")])
    except TelegramBadRequest:
        pass
    await refresh_poll(day)


@dp.poll_answer()
async def on_poll_answer(answer: PollAnswer):
    day_row = db.day_by_poll(answer.poll_id)
    if not day_row or day_row["closed"] or not answer.user:
        return
    u = answer.user
    db.save_user(u.id, u.full_name, u.username)
    # пустой список — человек отозвал голос
    portions = POLL_PORTIONS[answer.option_ids[0]] if answer.option_ids else 0
    db.set_order(day_row["day"], u.id, portions)


# ---------- команды ----------

@dp.message(Command("chatid"))
async def cmd_chatid(message: Message):
    await message.answer(f"chat_id: <code>{message.chat.id}</code>\nваш user_id: <code>{message.from_user.id}</code>")


@dp.message(Command("help", "start", "admin"), F.chat.type != "private")
async def cmd_help_group(message: Message):
    username = await bot_username()
    await message.reply(
        f"Каждый рабочий день в {setting('poll_time')} я открываю голосование за обед, "
        f"в {setting('report_time')} отправляю заявку на кухню.\n\n"
        f"Ваш долг, обеды и оплата — в личке: @{username} → Start.\n"
        f"Админам: @{username} → /admin")


@dp.message(Command("help"))
async def cmd_help_private(message: Message):
    await message.answer("/start — мой профиль: долг и обеды\n/pay — оплатить\n/admin — панель администратора")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    await message.answer(stats_text(*this_month()))


async def main():
    await bot.set_my_commands(
        [BotCommand(command="start", description="Мой профиль: долг и обеды"),
         BotCommand(command="pay", description="Оплатить обеды"),
         BotCommand(command="admin", description="Панель администратора")],
        scope=BotCommandScopeAllPrivateChats())
    schedule_jobs()
    scheduler.start()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
