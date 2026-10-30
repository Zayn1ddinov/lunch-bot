"""Общее: конфиг, настройки, база, бот, расписание и логика опроса."""
import calendar
import logging
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from db import DB

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
# можно сначала запустить только с токеном, узнать ID групп через /chatid и потом вписать
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID") or 0)
KITCHEN_CHAT_ID = int(os.getenv("KITCHEN_CHAT_ID") or 0)
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = ["", "январь", "февраль", "март", "апрель", "май", "июнь",
             "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]

# значения по умолчанию; после изменения в панели админа берутся из базы
DEFAULTS = {
    "poll_time": os.getenv("POLL_TIME", "09:00"),
    "remind_time": os.getenv("REMIND_TIME", "10:15"),
    "report_time": os.getenv("REPORT_TIME", "10:45"),
    "workdays": os.getenv("WORKDAYS", "mon,tue,wed,thu,fri"),
    "price": os.getenv("PRICE", "0"),
    "enabled": "1",
    "skip_day": "",
}

log = logging.getLogger("lunch_bot")

db = DB(os.getenv("DB_PATH", "lunch.db"))
bot = Bot(BOT_TOKEN)
scheduler = AsyncIOScheduler(timezone=TZ)


def setting(key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def workdays() -> list[str]:
    return [d for d in setting("workdays").split(",") if d in WEEKDAYS]


def price() -> int:
    return int(setting("price") or 0)


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " сум"


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def human_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%d.%m.%Y} ({WEEKDAYS_RU[d.weekday()]})"


def month_period(y: int, m: int) -> tuple[str, str]:
    return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def this_month() -> tuple[str, str]:
    now = datetime.now(TZ)
    return month_period(now.year, now.month)


def prev_month() -> tuple[str, str]:
    first = datetime.now(TZ).date().replace(day=1)
    last = first - timedelta(days=1)
    return month_period(last.year, last.month)


def period_title(d1: str, d2: str) -> str:
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    if a.day == 1 and (b + timedelta(days=1)).day == 1 and a.month == b.month and a.year == b.year:
        return f"{MONTHS_RU[a.month]} {a.year}"
    return f"{a:%d.%m.%Y} – {b:%d.%m.%Y}"


# ---------- права ----------

_admin_cache: dict[int, tuple[bool, float]] = {}


async def is_admin(user_id: int) -> bool:
    """Админ — из ADMIN_IDS или администратор группы сотрудников."""
    if user_id in ADMIN_IDS:
        return True
    cached = _admin_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < 300:
        return cached[0]
    try:
        member = await bot.get_chat_member(GROUP_CHAT_ID, user_id)
        ok = member.status in ("administrator", "creator")
    except TelegramBadRequest:
        ok = False
    _admin_cache[user_id] = (ok, time.monotonic())
    return ok


# ---------- тексты ----------

def order_list(day: str) -> tuple[str, int]:
    rows = db.day_orders(day)
    total = sum(r["portions"] for r in rows)
    lines = [f"{i}. {r['name']}" + (f" ×{r['portions']}" if r["portions"] > 1 else "")
             for i, r in enumerate(rows, 1)]
    return "\n".join(lines) or "пока никого", total


def poll_text(day: str, closed: bool = False, cancelled: bool = False) -> str:
    head = f"🍽 Обед {human_day(day)}\n\n"
    if cancelled:
        return head + "❌ Сегодня заявки на обед не собираются."
    lines, total = order_list(day)
    if closed:
        head += "🔒 Приём закрыт, заявка отправлена на кухню.\n\n"
    else:
        head += (f"Кто будет обедать в столовой — поставьте <b>+</b> в этот чат до {setting('report_time')}.\n"
                 "С гостем: <b>+2</b>. Передумали: <b>-</b>\n\n")
    return head + f"Записались ({total}):\n{lines}"


def kitchen_report(day: str) -> str:
    lines, total = order_list(day)
    return f"🍽 Обеды на {human_day(day)}\n\nИтого порций: {total}\n\n{lines}"


def stats_text(d1: str, d2: str) -> str:
    rows = db.stats(d1, d2)
    title = period_title(d1, d2)
    if not rows:
        return f"📈 За {title} обедов не было."
    p = price()
    lines = []
    for i, r in enumerate(rows, 1):
        line = f"{i}. {r['name']} — {r['days']} дн."
        if r["portions"] != r["days"]:
            line += f", {r['portions']} порц."
        if p:
            line += f" — {money(r['portions'] * p)}"
        lines.append(line)
    total = sum(r["portions"] for r in rows)
    text = f"📈 Обеды за {title}\n\n" + "\n".join(lines) + f"\n\nВсего порций: {total}"
    if p:
        text += f"\nНа сумму: {money(total * p)}"
    return text


# ---------- опрос ----------

async def refresh_poll(day: str, closed: bool = False, cancelled: bool = False):
    row = db.get_day(day)
    if not row or not row["message_id"]:
        return
    try:
        await bot.edit_message_text(poll_text(day, closed, cancelled), chat_id=row["chat_id"],
                                    message_id=row["message_id"], parse_mode="HTML")
    except TelegramBadRequest:
        pass  # текст не изменился или сообщение удалено


async def start_poll(manual: bool = False) -> bool:
    day = today()
    if db.get_day(day):
        return False
    if not manual and (setting("enabled") != "1" or setting("skip_day") == day):
        log.info("Опрос на %s пропущен (пауза или пропуск дня)", day)
        return False
    msg = await bot.send_message(GROUP_CHAT_ID, poll_text(day), parse_mode="HTML")
    db.open_day(day, GROUP_CHAT_ID, msg.message_id)
    try:
        await bot.pin_chat_message(GROUP_CHAT_ID, msg.message_id, disable_notification=True)
    except TelegramBadRequest:
        pass  # бот не админ — не страшно
    log.info("Опрос на %s опубликован", day)
    return True


async def scheduled_poll():
    await start_poll()


async def remind():
    day = today()
    row = db.get_day(day)
    if not row or row["closed"]:
        return
    _, total = order_list(day)
    await bot.send_message(
        GROUP_CHAT_ID,
        f"⏰ До {setting('report_time')} собираем заявки на обед. Пока записались: {total}. Кто ещё — ставьте +",
        reply_to_message_id=row["message_id"],
    )


async def _unpin(day: str):
    row = db.get_day(day)
    try:
        await bot.unpin_chat_message(GROUP_CHAT_ID, message_id=row["message_id"])
    except TelegramBadRequest:
        pass


async def send_report():
    day = today()
    if not db.is_open(day):
        log.info("Опрос на %s не открыт или уже закрыт", day)
        return
    db.close_day(day)
    await bot.send_message(KITCHEN_CHAT_ID, kitchen_report(day))
    await refresh_poll(day, closed=True)
    await _unpin(day)
    log.info("Отчёт на %s отправлен", day)


async def cancel_today():
    day = today()
    db.cancel_day(day)
    await refresh_poll(day, cancelled=True)
    await _unpin(day)


def schedule_jobs():
    """(Пере)создаёт задачи по текущим настройкам — вызывается при старте и после изменений в панели."""
    if not GROUP_CHAT_ID or not KITCHEN_CHAT_ID:
        log.warning("GROUP_CHAT_ID / KITCHEN_CHAT_ID не заданы — опросы не запускаются. Узнайте ID через /chatid")
        return
    days = ",".join(workdays())
    for job_id, key, func in (("poll", "poll_time", scheduled_poll),
                              ("remind", "remind_time", remind),
                              ("report", "report_time", send_report)):
        h, m = map(int, setting(key).split(":"))
        scheduler.add_job(func, CronTrigger(day_of_week=days, hour=h, minute=m, timezone=TZ),
                          id=job_id, replace_existing=True, misfire_grace_time=600)
    log.info("Расписание: опрос %s, напоминание %s, закрытие %s, дни %s",
             setting("poll_time"), setting("remind_time"), setting("report_time"), days)
