"""Общее: конфиг, настройки, база, бот, расписание, опрос, деньги, меню."""
import calendar
import logging
import os
import time
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
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
MENU_REMIND_TIME = os.getenv("MENU_REMIND_TIME", "15:00")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = ["", "январь", "февраль", "март", "апрель", "май", "июнь",
             "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_IN = ["", "январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]

# варианты в опросе Telegram → порций
POLL_OPTIONS = ["🍽 Буду обедать", "🍽 Буду + 1 гость", "❌ Не буду"]
POLL_PORTIONS = [1, 2, 0]

# значения по умолчанию; после изменения в панели админа берутся из базы
DEFAULTS = {
    "poll_time": os.getenv("POLL_TIME", "09:00"),
    "remind_time": os.getenv("REMIND_TIME", "10:15"),
    "report_time": os.getenv("REPORT_TIME", "10:45"),
    "workdays": os.getenv("WORKDAYS", "mon,tue,wed,thu,fri"),
    "price": os.getenv("PRICE", "0"),
    "enabled": "1",
    "skip_day": "",
    "vote_mode": "poll",          # poll — опрос Telegram, plus — плюсики в чате
    "card": "",                   # реквизиты для перевода
    "pay_remind_day": "fri",      # день напоминания должникам или off
    "pay_remind_time": "16:00",
}

log = logging.getLogger("lunch_bot")

db = DB(os.getenv("DB_PATH", "lunch.db"))
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
scheduler = AsyncIOScheduler(timezone=TZ)


def setting(key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def workdays() -> list[str]:
    return [d for d in setting("workdays").split(",") if d in WEEKDAYS]


def price() -> int:
    return int(setting("price") or 0)


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " сум"


def kb(*rows) -> InlineKeyboardMarkup:
    """kb([("текст", "callback"), ...], ...) — инлайн-клавиатура из строк кнопок."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows if row])


# ---------- даты ----------

def today_date() -> date:
    return datetime.now(TZ).date()


def today() -> str:
    return today_date().isoformat()


def human_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%d.%m.%Y} ({WEEKDAYS_RU[d.weekday()]})"


def short_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}"


def month_period(y: int, m: int) -> tuple[str, str]:
    return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def this_month() -> tuple[str, str]:
    d = today_date()
    return month_period(d.year, d.month)


def prev_month() -> tuple[str, str]:
    last = today_date().replace(day=1) - timedelta(days=1)
    return month_period(last.year, last.month)


def period_title(d1: str, d2: str) -> str:
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    if a.day == 1 and (b + timedelta(days=1)).day == 1 and a.month == b.month and a.year == b.year:
        return f"{MONTHS_RU[a.month]} {a.year}"
    return f"{a:%d.%m.%Y} – {b:%d.%m.%Y}"


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_workdays(monday: date) -> list[str]:
    days = workdays()
    return [(monday + timedelta(days=i)).isoformat() for i in range(7) if WEEKDAYS[i] in days]


# ---------- права и уведомления ----------

_admin_cache: dict[int, tuple[bool, float]] = {}
_group_admins: tuple[set[int], float] = (set(), 0.0)


async def group_admins() -> set[int]:
    global _group_admins
    if time.monotonic() - _group_admins[1] < 300 or not GROUP_CHAT_ID:
        return _group_admins[0]
    try:
        admins = await bot.get_chat_administrators(GROUP_CHAT_ID)
        ids = {a.user.id for a in admins if not a.user.is_bot}
    except (TelegramBadRequest, TelegramForbiddenError):
        ids = set()
    _group_admins = (ids, time.monotonic())
    return ids


async def is_admin(user_id: int) -> bool:
    """Админ — из ADMIN_IDS или администратор группы сотрудников."""
    if user_id in ADMIN_IDS:
        return True
    cached = _admin_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < 300:
        return cached[0]
    ok = False
    if GROUP_CHAT_ID:
        try:
            member = await bot.get_chat_member(GROUP_CHAT_ID, user_id)
            ok = member.status in ("administrator", "creator")
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
    _admin_cache[user_id] = (ok, time.monotonic())
    return ok


async def send_dm(user_id: int, text: str, **kwargs) -> bool:
    """Личное сообщение; False — если человек не нажимал Start или заблокировал бота."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        db.set_can_dm(user_id, False)
        return False


async def notify_admins(text: str, photo: str | None = None, document: str | None = None,
                        reply_markup=None) -> int:
    sent = 0
    for admin_id in ADMIN_IDS | await group_admins():
        try:
            if photo:
                await bot.send_photo(admin_id, photo, caption=text, reply_markup=reply_markup)
            elif document:
                await bot.send_document(admin_id, document, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(admin_id, text, reply_markup=reply_markup)
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            pass  # админ не открывал бота в личке
    return sent


_me = None


async def bot_username() -> str:
    global _me
    if _me is None:
        _me = await bot.get_me()
    return _me.username


# ---------- деньги ----------

def debt_of(user_id: int) -> tuple[int, int]:
    """(долг — может быть отрицательным при переплате, сумма на проверке)."""
    charged, paid, pending = db.balance(user_id, price())
    return charged - paid, pending


def balance_text(user_id: int) -> str:
    debt, pending = debt_of(user_id)
    if debt > 0:
        text = f"💰 Долг: <b>{money(debt)}</b>"
    elif debt < 0:
        text = f"💰 Переплата: <b>{money(-debt)}</b>"
    else:
        text = "💰 Долга нет ✅"
    if pending:
        text += f"\n⏳ На проверке у администратора: {money(pending)}"
    return text


def card_text() -> str:
    card = setting("card")
    return f"💳 Перевод на карту:\n<code>{escape(card)}</code>" if card else "💳 Реквизиты для оплаты уточните у администратора."


PAY_BUTTON = kb([("💳 Я оплатил — отправить скриншот", "u:pay")])


async def remind_debtors() -> tuple[int, list[str]]:
    """Рассылает напоминание всем с долгом. Возвращает (отправлено, кому не удалось)."""
    if not price():
        return 0, []
    sent, failed = 0, []
    for r in db.balances(price()):
        if r["debt"] <= 0:
            continue
        text = (f"🔔 <b>Напоминание об оплате обедов</b>\n\n{balance_text(r['user_id'])}\n\n{card_text()}\n\n"
                "После перевода нажмите кнопку ниже и пришлите скриншот.")
        if await send_dm(r["user_id"], text, reply_markup=PAY_BUTTON):
            sent += 1
        else:
            failed.append(r["name"])
    return sent, failed


async def scheduled_pay_reminder():
    sent, failed = await remind_debtors()
    if failed:
        await notify_admins("🔔 Напоминание об оплате отправлено: " + str(sent) +
                            "\n\nНе получили (не нажимали Start у бота):\n" +
                            "\n".join("• " + escape(n) for n in failed))


# ---------- тексты опроса ----------

def order_list(day: str) -> tuple[str, int]:
    rows = db.day_orders(day)
    total = sum(r["portions"] for r in rows)
    lines = [f"{i}. {escape(r['name'])}" + (f" ×{r['portions']}" if r["portions"] > 1 else "")
             for i, r in enumerate(rows, 1)]
    return "\n".join(lines) or "пока никого", total


def menu_block(day: str) -> str:
    menu = db.get_menu(day)
    return f"\n\n🍲 <b>Меню:</b>\n{escape(menu)}" if menu else ""


def poll_text(day: str, closed: bool = False, cancelled: bool = False) -> str:
    """Текст сообщения в режиме плюсиков (обновляется по мере записи)."""
    head = f"🍽 <b>Обед {human_day(day)}</b>"
    if cancelled:
        return head + "\n\n❌ Сегодня заявки на обед не собираются."
    head += menu_block(day) + "\n\n"
    lines, total = order_list(day)
    if closed:
        head += "🔒 Приём закрыт, заявка отправлена на кухню.\n\n"
    else:
        head += (f"Кто будет обедать — поставьте <b>+</b> в этот чат до {setting('report_time')}.\n"
                 "С гостем: <b>+2</b>. Передумали: <b>-</b>\n\n")
    return head + f"Записались ({total}):\n{lines}"


def kitchen_report(day: str) -> str:
    lines, total = order_list(day)
    return f"🍽 <b>Обеды на {human_day(day)}</b>\n\nИтого порций: <b>{total}</b>\n\n{lines}"


def stats_text(d1: str, d2: str) -> str:
    rows = db.stats(d1, d2, price())
    title = period_title(d1, d2)
    if not rows:
        return f"📈 За {title} обедов не было."
    lines = []
    for i, r in enumerate(rows, 1):
        line = f"{i}. {escape(r['name'])} — {r['days']} дн."
        if r["portions"] != r["days"]:
            line += f", {r['portions']} порц."
        if r["charged"]:
            line += f" — {money(r['charged'])}"
        lines.append(line)
    total = sum(r["portions"] for r in rows)
    charged = sum(r["charged"] or 0 for r in rows)
    text = f"📈 <b>Обеды за {title}</b>\n\n" + "\n".join(lines) + f"\n\nВсего порций: {total}"
    if charged:
        text += f"\nНа сумму: {money(charged)}"
    return text


# ---------- опрос ----------

async def refresh_poll(day: str, closed: bool = False, cancelled: bool = False):
    """Обновляет сообщение со списком (только режим плюсиков)."""
    row = db.get_day(day)
    if not row or not row["message_id"] or row["poll_id"]:
        return
    try:
        await bot.edit_message_text(poll_text(day, closed, cancelled), chat_id=row["chat_id"],
                                    message_id=row["message_id"])
    except TelegramBadRequest:
        pass  # текст не изменился или сообщение удалено


async def start_poll(manual: bool = False) -> bool:
    day = today()
    if db.get_day(day) or not GROUP_CHAT_ID:
        return False
    if not manual and (setting("enabled") != "1" or setting("skip_day") == day):
        log.info("Опрос на %s пропущен (пауза или пропуск дня)", day)
        return False
    username = await bot_username()
    if setting("vote_mode") == "poll":
        await bot.send_message(
            GROUP_CHAT_ID,
            f"🍽 <b>Обед {human_day(day)}</b>{menu_block(day)}\n\n"
            f"Голосуйте в опросе ниже до {setting('report_time')} (или просто напишите <b>+</b> в чат).\n"
            f"Свой долг и обеды смотрите в боте @{username} — нажмите там Start.")
        msg = await bot.send_poll(GROUP_CHAT_ID, f"Будете обедать сегодня, {short_day(day)}?",
                                  options=POLL_OPTIONS, is_anonymous=False)
        db.open_day(day, GROUP_CHAT_ID, msg.message_id, price(), msg.poll.id)
    else:
        msg = await bot.send_message(GROUP_CHAT_ID, poll_text(day) +
                                     f"\n\nДолг и обеды — в боте @{username}")
        db.open_day(day, GROUP_CHAT_ID, msg.message_id, price())
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
    action = "голосуйте в опросе" if row["poll_id"] else "ставьте +"
    await bot.send_message(
        GROUP_CHAT_ID,
        f"⏰ До {setting('report_time')} собираем заявки на обед. Пока записались: {total}. Кто ещё — {action}",
        reply_to_message_id=row["message_id"],
    )


async def _finish_group_message(day: str, text: str):
    row = db.get_day(day)
    if row["poll_id"]:
        try:
            await bot.stop_poll(row["chat_id"], row["message_id"])
        except TelegramBadRequest:
            pass
        await bot.send_message(row["chat_id"], text, reply_to_message_id=row["message_id"])
    try:
        await bot.unpin_chat_message(row["chat_id"], message_id=row["message_id"])
    except TelegramBadRequest:
        pass


async def send_report():
    day = today()
    if not db.is_open(day):
        log.info("Опрос на %s не открыт или уже закрыт", day)
        return
    db.close_day(day, price())
    await bot.send_message(KITCHEN_CHAT_ID, kitchen_report(day))
    lines, total = order_list(day)
    await refresh_poll(day, closed=True)
    await _finish_group_message(day, f"🔒 Приём закрыт. На кухню отправлено порций: <b>{total}</b>\n\n{lines}")
    log.info("Отчёт на %s отправлен", day)


async def cancel_today():
    day = today()
    db.cancel_day(day)
    await refresh_poll(day, cancelled=True)
    await _finish_group_message(day, "❌ Сегодня обед отменён, заявки не собираются.")


# ---------- меню ----------

def week_menu_text(monday: date) -> str:
    days = week_workdays(monday)
    lines = []
    for d in days:
        menu = db.get_menu(d)
        lines.append(f"<b>{short_day(d)}</b>\n{escape(menu) if menu else '— не заполнено'}")
    sunday = monday + timedelta(days=6)
    return f"🍲 <b>Меню {monday:%d.%m} – {sunday:%d.%m}</b>\n\n" + "\n\n".join(lines)


async def remind_menu():
    """В последний рабочий день недели напоминает админам заполнить меню на следующую."""
    monday = week_start(today_date()) + timedelta(days=7)
    missing = [short_day(d) for d in week_workdays(monday) if not db.get_menu(d)]
    if missing:
        await notify_admins(
            f"🍲 Не заполнено меню на следующую неделю: {', '.join(missing)}",
            reply_markup=kb([("🍲 Заполнить меню", f"adm:mweek:{monday.isoformat()}")]))


# ---------- расписание ----------

def schedule_jobs():
    """(Пере)создаёт задачи по текущим настройкам — при старте и после изменений в панели."""
    if not GROUP_CHAT_ID or not KITCHEN_CHAT_ID:
        log.warning("GROUP_CHAT_ID / KITCHEN_CHAT_ID не заданы — опросы не запускаются. Узнайте ID через /chatid")
        return
    days = ",".join(workdays())

    def add(job_id, func, hhmm, dow):
        h, m = map(int, hhmm.split(":"))
        scheduler.add_job(func, CronTrigger(day_of_week=dow, hour=h, minute=m, timezone=TZ),
                          id=job_id, replace_existing=True, misfire_grace_time=600)

    add("poll", scheduled_poll, setting("poll_time"), days)
    add("remind", remind, setting("remind_time"), days)
    add("report", send_report, setting("report_time"), days)
    add("menu", remind_menu, MENU_REMIND_TIME, workdays()[-1])
    if setting("pay_remind_day") in WEEKDAYS:
        add("payremind", scheduled_pay_reminder, setting("pay_remind_time"), setting("pay_remind_day"))
    elif scheduler.get_job("payremind"):
        scheduler.remove_job("payremind")
    log.info("Расписание: опрос %s, напоминание %s, закрытие %s, дни %s, оплата %s %s",
             setting("poll_time"), setting("remind_time"), setting("report_time"), days,
             setting("pay_remind_day"), setting("pay_remind_time"))
