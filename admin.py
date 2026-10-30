"""Панель администратора: /admin в личке с ботом."""
import re
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup, Message)

from core import (WEEKDAYS, WEEKDAYS_SHORT, cancel_today, db, is_admin, kitchen_report, money,
                  month_period, prev_month, price, schedule_jobs, send_report, setting, start_poll, stats_text,
                  this_month, today, workdays)
from excel import build_excel

router = Router()
router.message.filter(F.chat.type == "private")

TIME_LABELS = {
    "poll_time": "🕘 время опроса",
    "remind_time": "⏰ время напоминания",
    "report_time": "🔒 время закрытия и отправки на кухню",
}
TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.](\d{2})$")


class Input(StatesGroup):
    time = State()
    price = State()
    period = State()


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[Btn(text=t, callback_data=d) for t, d in row] for row in rows])


BACK = [("« Назад", "adm:menu")]


def today_status() -> str:
    day = today()
    row = db.get_day(day)
    if row is None:
        if setting("enabled") != "1":
            return "опросы на паузе"
        if setting("skip_day") == day:
            return "сегодня пропускается"
        if WEEKDAYS[date.fromisoformat(day).weekday()] not in workdays():
            return "сегодня нерабочий день"
        return f"опрос откроется в {setting('poll_time')}"
    if row["closed"]:
        return "отменён" if setting("skip_day") == day else "заявка отправлена на кухню"
    total = sum(x["portions"] for x in db.day_orders(day))
    return f"опрос открыт, записались {total}"


def menu_text() -> str:
    days = " ".join(WEEKDAYS_SHORT[WEEKDAYS.index(d)] for d in workdays())
    p = price()
    return (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"🕘 Опрос: <b>{setting('poll_time')}</b>\n"
        f"⏰ Напоминание: <b>{setting('remind_time')}</b>\n"
        f"🔒 Закрытие и отправка на кухню: <b>{setting('report_time')}</b>\n"
        f"📅 Дни: <b>{days}</b>\n"
        f"💰 Цена обеда: <b>{money(p) if p else 'не задана'}</b>\n"
        f"🔔 Опросы: <b>{'включены' if setting('enabled') == '1' else 'на паузе'}</b>\n\n"
        f"Сегодня: {today_status()}"
    )


def menu_kb() -> InlineKeyboardMarkup:
    day = today()
    row = db.get_day(day)
    if row is None:
        skipped = setting("skip_day") == day
        today_btn = [("↩️ Не пропускать сегодня" if skipped else "⏭ Пропустить сегодня", "adm:skip")]
    elif not row["closed"]:
        today_btn = [("❌ Отменить сегодня", "adm:cancel")]
    else:
        today_btn = []  # день уже закрыт
    pause_btn = ("⏸ Пауза", "adm:pause") if setting("enabled") == "1" else ("▶️ Включить опросы", "adm:pause")
    return kb(
        [("📊 Сегодня", "adm:today"), ("📈 Отчёты и Excel", "adm:reports")],
        [("🕘 Опрос", "adm:time:poll_time"), ("⏰ Напомин.", "adm:time:remind_time"),
         ("🔒 Закрытие", "adm:time:report_time")],
        [("📅 Дни недели", "adm:days"), ("💰 Цена", "adm:price")],
        [("▶️ Открыть опрос", "adm:open"), ("📤 Отправить на кухню", "adm:send")],
        today_btn + [pause_btn],
        [("🔄 Обновить", "adm:menu")],
    )


async def show_menu(target: Message | CallbackQuery):
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(menu_text(), reply_markup=menu_kb(), parse_mode="HTML")
    else:
        await target.answer(menu_text(), reply_markup=menu_kb(), parse_mode="HTML")


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("Панель доступна только администраторам группы.")
        return
    await state.clear()
    await show_menu(message)


# все кнопки панели — только для админов
@router.callback_query(F.data.startswith("adm:"))
async def on_admin_button(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        await call.answer("Только для администраторов", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[1]
    await state.clear()

    if action == "menu":
        await show_menu(call)

    elif action == "today":
        day = today()
        text = kitchen_report(day) if db.get_day(day) else "Сегодня опрос ещё не открывался."
        await call.message.edit_text(text, reply_markup=kb(BACK))

    elif action == "time":
        key = parts[2]
        await state.set_state(Input.time)
        await state.update_data(key=key)
        await call.message.edit_text(
            f"Сейчас {TIME_LABELS[key]}: <b>{setting(key)}</b>\n\nОтправьте новое время, например <b>10:30</b>",
            reply_markup=kb(BACK), parse_mode="HTML")

    elif action == "price":
        await state.set_state(Input.price)
        await call.message.edit_text(
            "Отправьте цену одного обеда в сумах, например <b>25000</b>.\n0 — не считать суммы.",
            reply_markup=kb(BACK), parse_mode="HTML")

    elif action == "days":
        await show_days(call)

    elif action == "day":
        days = workdays()
        d = parts[2]
        if d in days:
            if len(days) == 1:
                await call.answer("Нужен хотя бы один день", show_alert=True)
                return
            days.remove(d)
        else:
            days.append(d)
        db.set_setting("workdays", ",".join(x for x in WEEKDAYS if x in days))
        schedule_jobs()
        await show_days(call)

    elif action == "open":
        ok = await start_poll(manual=True)
        await call.answer("Опрос открыт в группе" if ok else "Опрос на сегодня уже был", show_alert=not ok)
        await show_menu(call)

    elif action == "send":
        if not db.is_open(today()):
            await call.answer("Нет открытого опроса", show_alert=True)
            return
        await send_report()
        await call.answer("Заявка отправлена на кухню")
        await show_menu(call)

    elif action == "cancel":
        db.set_setting("skip_day", today())
        await cancel_today()
        await call.answer("Сегодняшний опрос отменён")
        await show_menu(call)

    elif action == "skip":
        day = today()
        db.set_setting("skip_day", "" if setting("skip_day") == day else day)
        await show_menu(call)

    elif action == "pause":
        db.set_setting("enabled", "0" if setting("enabled") == "1" else "1")
        await show_menu(call)

    elif action == "reports":
        await call.message.edit_text("📈 За какой период?", reply_markup=kb(
            [("Этот месяц", "adm:rep:{}:{}".format(*this_month())),
             ("Прошлый месяц", "adm:rep:{}:{}".format(*prev_month()))],
            [("📆 Указать период", "adm:period")],
            BACK))

    elif action == "period":
        await state.set_state(Input.period)
        await call.message.edit_text(
            "Отправьте период: <b>01.10.2026 15.10.2026</b>\nили месяц: <b>10.2026</b>",
            reply_markup=kb(BACK), parse_mode="HTML")

    elif action == "rep":
        d1, d2 = parts[2], parts[3]
        await call.message.edit_text(stats_text(d1, d2), reply_markup=report_kb(d1, d2))

    elif action == "xls":
        d1, d2 = parts[2], parts[3]
        await call.answer("Готовлю файл…")
        await call.message.answer_document(
            BufferedInputFile(build_excel(d1, d2), filename=f"obedy_{d1}_{d2}.xlsx"),
            caption=f"📥 Обеды {date.fromisoformat(d1):%d.%m.%Y} – {date.fromisoformat(d2):%d.%m.%Y}")
        return

    await call.answer()


def report_kb(d1: str, d2: str) -> InlineKeyboardMarkup:
    return kb([("📥 Скачать Excel", f"adm:xls:{d1}:{d2}")], [("« К периодам", "adm:reports")], BACK)


async def show_days(call: CallbackQuery):
    days = workdays()
    buttons = [(("✅ " if d in days else "▫️ ") + WEEKDAYS_SHORT[i], f"adm:day:{d}") for i, d in enumerate(WEEKDAYS)]
    await call.message.edit_text("📅 В какие дни собирать заявки? Нажмите, чтобы включить/выключить.",
                                 reply_markup=kb(buttons[:4], buttons[4:], BACK))


# ---------- ввод значений ----------

@router.message(Input.time, F.text)
async def got_time(message: Message, state: FSMContext):
    m = TIME_RE.match(message.text.strip())
    if not m:
        await message.answer("Не понял время. Формат: 10:30", reply_markup=kb(BACK))
        return
    value = f"{int(m.group(1)):02d}:{m.group(2)}"
    if int(m.group(2)) > 59:
        await message.answer("Минуты — от 00 до 59.", reply_markup=kb(BACK))
        return
    key = (await state.get_data())["key"]
    times = {k: setting(k) for k in TIME_LABELS}
    times[key] = value
    if not times["poll_time"] < times["remind_time"] < times["report_time"]:
        await message.answer(
            f"Должно быть: опрос < напоминание < закрытие.\nПолучится: {times['poll_time']} → "
            f"{times['remind_time']} → {times['report_time']}. Отправьте другое время.",
            reply_markup=kb(BACK))
        return
    db.set_setting(key, value)
    schedule_jobs()
    await state.clear()
    await message.answer(f"✅ Сохранено: {TIME_LABELS[key]} — {value}")
    await show_menu(message)


@router.message(Input.price, F.text)
async def got_price(message: Message, state: FSMContext):
    value = re.sub(r"[\s_.,]", "", message.text).removesuffix("сум")
    if not value.isdigit():
        await message.answer("Отправьте число, например 25000", reply_markup=kb(BACK))
        return
    db.set_setting("price", str(int(value)))
    await state.clear()
    await message.answer("✅ Цена сохранена")
    await show_menu(message)


def parse_date(s: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


@router.message(Input.period, F.text)
async def got_period(message: Message, state: FSMContext):
    parts = message.text.replace("-", " ").replace("–", " ").split() if "." in message.text else message.text.split()
    try:
        if len(parts) == 2:
            a, b = sorted((parse_date(parts[0]), parse_date(parts[1])))
            d1, d2 = a.isoformat(), b.isoformat()
        elif len(parts) == 1 and re.fullmatch(r"\d{1,2}\.\d{4}", parts[0]):
            m, y = map(int, parts[0].split("."))
            d1, d2 = month_period(y, m)
        else:
            raise ValueError
    except ValueError:
        await message.answer("Не понял. Пример: 01.10.2026 15.10.2026 или 10.2026", reply_markup=kb(BACK))
        return
    await state.clear()
    await message.answer(stats_text(d1, d2), reply_markup=report_kb(d1, d2))
