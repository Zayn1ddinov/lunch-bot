"""Панель администратора: /admin в личке с ботом. Плюс кнопки подтверждения оплат."""
import re
from datetime import date, datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from core import (WEEKDAYS, WEEKDAYS_SHORT, balance_text, cancel_today, db, debt_of, is_admin, kb,
                  kitchen_report, money, month_period, prev_month, price, remind_debtors, schedule_jobs,
                  send_dm, send_report, setting, short_day, start_poll, stats_text, this_month, today,
                  today_date, week_menu_text, week_start, week_workdays, workdays)
from excel import build_excel

router = Router()
router.message.filter(F.chat.type == "private")

TIME_LABELS = {
    "poll_time": "🕘 время опроса",
    "remind_time": "⏰ время напоминания",
    "report_time": "🔒 время закрытия и отправки на кухню",
    "pay_remind_time": "🔔 время напоминания об оплате",
}
TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")
PAGE = 10


class Input(StatesGroup):
    time = State()
    price = State()
    card = State()
    period = State()
    uname = State()
    ucash = State()
    menu = State()


BACK = [("« В меню", "adm:menu")]


async def show(target: Message | CallbackQuery, text: str, markup=None):
    """Показать экран: в колбэке — редактируем сообщение, иначе (или если это фото) — новое."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            target = target.message
    await target.answer(text, reply_markup=markup)


# ---------- главное меню ----------

def today_status() -> str:
    day = today()
    row = db.get_day(day)
    if row is None:
        if setting("enabled") != "1":
            return "опросы на паузе"
        if setting("skip_day") == day:
            return "сегодня пропускается"
        if WEEKDAYS[today_date().weekday()] not in workdays():
            return "нерабочий день"
        return f"опрос откроется в {setting('poll_time')}"
    if row["closed"]:
        return "отменён" if setting("skip_day") == day else "заявка отправлена на кухню"
    return f"опрос открыт, записались {sum(r['portions'] for r in db.day_orders(day))}"


def menu_text() -> str:
    p = price()
    debts = [r["debt"] for r in db.balances(p) if r["debt"] > 0] if p else []
    pending = db.pending_payments()
    text = (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"Сегодня: {today_status()}\n"
        f"Цена обеда: {money(p) if p else 'не задана'}\n"
    )
    if p:
        text += f"Должников: {len(debts)} на {money(sum(debts))}\n"
    if pending:
        text += f"\n⏳ <b>Оплат ждут проверки: {len(pending)}</b>"
    return text


def menu_kb():
    day = today()
    row = db.get_day(day)
    if row is None:
        today_btn = [("↩️ Не пропускать сегодня" if setting("skip_day") == day else "⏭ Пропустить сегодня", "adm:skip")]
    elif not row["closed"]:
        today_btn = [("❌ Отменить сегодня", "adm:cancel")]
    else:
        today_btn = []
    pending = len(db.pending_payments())
    return kb(
        [("📊 Сегодня", "adm:today"), ("📈 Отчёты и Excel", "adm:reports")],
        [("👥 Сотрудники", "adm:users:0"), ("💰 Долги", "adm:debts")],
        [(f"💳 Оплаты на проверке ({pending})" if pending else "💳 Оплаты", "adm:pays")],
        [("🍲 Меню на неделю", "adm:mweek:" + week_start(today_date()).isoformat()), ("⚙️ Настройки", "adm:settings")],
        [("▶️ Открыть опрос", "adm:open"), ("📤 Отправить на кухню", "adm:send")],
        today_btn + [("⏸ Пауза", "adm:pause") if setting("enabled") == "1" else ("▶️ Включить опросы", "adm:pause")],
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("Панель доступна только администраторам группы.")
        return
    await state.clear()
    await message.answer(menu_text(), reply_markup=menu_kb())


# ---------- настройки ----------

def settings_text() -> str:
    days = " ".join(WEEKDAYS_SHORT[WEEKDAYS.index(d)] for d in workdays())
    p, card = price(), setting("card")
    prd = setting("pay_remind_day")
    pay_remind = (f"{WEEKDAYS_SHORT[WEEKDAYS.index(prd)]} {setting('pay_remind_time')}"
                  if prd in WEEKDAYS else "выключено")
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"🕘 Опрос: <b>{setting('poll_time')}</b>\n"
        f"⏰ Напоминание: <b>{setting('remind_time')}</b>\n"
        f"🔒 Закрытие и отправка на кухню: <b>{setting('report_time')}</b>\n"
        f"📅 Дни: <b>{days}</b>\n"
        f"🗳 Голосование: <b>{'опрос Telegram' if setting('vote_mode') == 'poll' else 'плюсики в чате'}</b>\n"
        f"💰 Цена обеда: <b>{money(p) if p else 'не задана'}</b>\n"
        f"💳 Карта: <b>{escape(card) if card else 'не указана'}</b>\n"
        f"🔔 Напоминание должникам: <b>{pay_remind}</b>"
    )


def settings_kb():
    return kb(
        [("🕘 Опрос", "adm:time:poll_time"), ("⏰ Напомин.", "adm:time:remind_time"),
         ("🔒 Закрытие", "adm:time:report_time")],
        [("📅 Дни недели", "adm:days"), ("🗳 Сменить голосование", "adm:vote")],
        [("💰 Цена обеда", "adm:price"), ("💳 Номер карты", "adm:card")],
        [("🔔 Напоминание об оплате", "adm:payrem")],
        BACK,
    )


async def show_days(call: CallbackQuery):
    days = workdays()
    buttons = [(("✅ " if d in days else "▫️ ") + WEEKDAYS_SHORT[i], f"adm:day:{d}") for i, d in enumerate(WEEKDAYS)]
    await show(call, "📅 В какие дни собирать заявки? Нажмите, чтобы включить/выключить.",
               kb(buttons[:4], buttons[4:], [("« Настройки", "adm:settings")]))


async def show_payrem(call: CallbackQuery):
    cur = setting("pay_remind_day")
    buttons = [(("✅ " if d == cur else "") + WEEKDAYS_SHORT[i], f"adm:prday:{d}") for i, d in enumerate(WEEKDAYS)]
    await show(call,
               "🔔 <b>Напоминание должникам</b>\n\nРаз в неделю бот пишет в личку каждому, у кого есть долг: "
               "сумму, номер карты и кнопку «отправить скриншот».\n\nВыберите день:",
               kb(buttons[:4], buttons[4:],
                  [(("✅ " if cur == "off" else "") + "Выключить", "adm:prday:off"),
                   (f"🕘 Время: {setting('pay_remind_time')}", "adm:time:pay_remind_time")],
                  [("📣 Напомнить должникам сейчас", "adm:remindnow")],
                  [("« Настройки", "adm:settings")]))


# ---------- сотрудники ----------

async def show_users(call: CallbackQuery, page: int):
    users = db.all_users()
    if not users:
        await show(call, "Пока никого нет. Сотрудники появятся, когда проголосуют или нажмут Start у бота.", kb(BACK))
        return
    p = price()
    debts = {r["user_id"]: r["debt"] for r in db.balances(p)} if p else {}
    chunk = users[page * PAGE:(page + 1) * PAGE]
    rows = [[(("❗️" if not u["official_name"] else "") + u["display"]
              + (f" — {money(debts[u['user_id']])}" if debts.get(u["user_id"], 0) > 0 else ""),
              f"adm:user:{u['user_id']}")] for u in chunk]
    nav = []
    if page > 0:
        nav.append(("◀️", f"adm:users:{page - 1}"))
    if (page + 1) * PAGE < len(users):
        nav.append(("▶️", f"adm:users:{page + 1}"))
    await show(call, f"👥 <b>Сотрудники</b> ({len(users)})\n❗️ — не указаны официальные имя и фамилия\n\n"
                     "Нажмите на человека, чтобы изменить имя или внести оплату.",
               kb(*rows, nav, BACK))


def user_card(user_id: int) -> str:
    u = db.get_user(user_id)
    d1, d2 = this_month()
    lunches = db.user_lunches(user_id, d1, d2)
    return (
        f"👤 <b>{escape(u['display'])}</b>\n\n"
        f"Официальное имя: {escape(u['official_name']) if u['official_name'] else '— не указано'}\n"
        f"В Telegram: {escape(u['name'])}" + (f" (@{u['username']})" if u["username"] else "") + "\n"
        f"Бот в личке: {'✅ запущен' if u['can_dm'] else '— не нажимал Start'}\n\n"
        f"{balance_text(user_id)}\n"
        f"📅 Обедов в этом месяце: {len(lunches)}"
    )


def user_kb(user_id: int):
    return kb([("✏️ Имя и фамилия", f"adm:uname:{user_id}"), ("💵 Внести оплату", f"adm:ucash:{user_id}")],
              [("« К списку", "adm:users:0")])


# ---------- оплаты ----------

async def send_payment_card(message: Message, p):
    caption = (f"💳 <b>Оплата #{p['id']}</b> от {p['created_at'][:16].replace('T', ' ')}\n\n"
               f"👤 {escape(p['name'])}\n💵 Сумма: <b>{money(p['amount'])}</b>\n"
               f"💰 Текущий долг: {money(debt_of(p['user_id'])[0])}")
    buttons = kb([("✅ Подтвердить", f"pay:ok:{p['id']}"), ("❌ Отклонить", f"pay:no:{p['id']}")])
    if p["file_type"] == "photo":
        await message.answer_photo(p["file_id"], caption=caption, reply_markup=buttons)
    elif p["file_type"] == "document":
        await message.answer_document(p["file_id"], caption=caption, reply_markup=buttons)
    else:
        await message.answer(caption, reply_markup=buttons)


@router.callback_query(F.data.startswith("pay:"))
async def on_payment_decision(call: CallbackQuery):
    if not await is_admin(call.from_user.id):
        await call.answer("Только для администраторов", show_alert=True)
        return
    _, decision, pid = call.data.split(":")
    status = "confirmed" if decision == "ok" else "rejected"
    p = db.get_payment(int(pid))
    if not db.decide_payment(int(pid), status, call.from_user.id):
        await call.answer("Эта оплата уже обработана", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=None)
        return
    mark = "✅ Подтверждено" if status == "confirmed" else "❌ Отклонено"
    note = f"\n\n{mark} — {escape(call.from_user.full_name)}"
    try:
        if call.message.caption is not None:
            await call.message.edit_caption(caption=call.message.html_text + note, reply_markup=None)
        else:
            await call.message.edit_text(call.message.html_text + note, reply_markup=None)
    except Exception:
        pass
    if status == "confirmed":
        await send_dm(p["user_id"], f"✅ Оплата <b>{money(p['amount'])}</b> подтверждена. Спасибо!\n\n"
                                    f"{balance_text(p['user_id'])}")
    else:
        await send_dm(p["user_id"], f"❌ Оплата <b>{money(p['amount'])}</b> не подтверждена администратором.\n"
                                    "Если это ошибка — свяжитесь с ним или отправьте скриншот ещё раз: /pay")
    await call.answer(mark)


# ---------- меню на неделю ----------

async def show_week(target, monday: date):
    days = week_workdays(monday)
    buttons = [(("✅ " if db.get_menu(d) else "▫️ ") + short_day(d), f"adm:mday:{d}") for d in days]
    prev_w, next_w = monday - timedelta(days=7), monday + timedelta(days=7)
    await show(target, week_menu_text(monday) + "\n\nНажмите на день, чтобы изменить.",
               kb(buttons[:4], buttons[4:],
                  [("◀️ Пред. неделя", f"adm:mweek:{prev_w}"), ("След. неделя ▶️", f"adm:mweek:{next_w}")],
                  [("📋 Скопировать с прошлой недели", f"adm:mcopy:{monday}")],
                  BACK))


# ---------- все кнопки панели ----------

@router.callback_query(F.data.startswith("adm:"))
async def on_admin_button(call: CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id):
        await call.answer("Только для администраторов", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[1]
    await state.clear()

    if action == "menu":
        await show(call, menu_text(), menu_kb())

    elif action == "today":
        day = today()
        text = kitchen_report(day) if db.get_day(day) else "Сегодня опрос ещё не открывался."
        await show(call, text, kb(BACK))

    elif action == "settings":
        await show(call, settings_text(), settings_kb())

    elif action == "time":
        key = parts[2]
        await state.set_state(Input.time)
        await state.update_data(key=key)
        await show(call, f"Сейчас {TIME_LABELS[key]}: <b>{setting(key)}</b>\n\nОтправьте новое время, например <b>10:30</b>",
                   kb([("« Настройки", "adm:settings")]))

    elif action == "price":
        await state.set_state(Input.price)
        await show(call, "Отправьте цену одного обеда в сумах, например <b>25000</b>.\n0 — не считать деньги.\n\n"
                         "Новая цена действует для сегодняшнего и будущих обедов, прошлые не пересчитываются.",
                   kb([("« Настройки", "adm:settings")]))

    elif action == "card":
        await state.set_state(Input.card)
        await show(call, "Отправьте номер карты и получателя, например:\n<b>8600 1234 5678 9012 Иванов И.</b>\n\n"
                         "Его увидят сотрудники при оплате. Отправьте <b>-</b>, чтобы убрать.",
                   kb([("« Настройки", "adm:settings")]))

    elif action == "vote":
        db.set_setting("vote_mode", "plus" if setting("vote_mode") == "poll" else "poll")
        await call.answer("Изменится со следующего опроса")
        await show(call, settings_text(), settings_kb())

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

    elif action == "payrem":
        await show_payrem(call)

    elif action == "prday":
        db.set_setting("pay_remind_day", parts[2])
        schedule_jobs()
        await show_payrem(call)

    elif action == "remindnow":
        await call.answer("Рассылаю…")
        sent, failed = await remind_debtors()
        text = f"📣 Напоминание отправлено: {sent}"
        if failed:
            text += "\n\nНе получили (не нажимали Start у бота):\n" + "\n".join("• " + escape(n) for n in failed)
        await call.message.answer(text, reply_markup=kb(BACK))
        return

    elif action == "open":
        ok = await start_poll(manual=True)
        await call.answer("Опрос открыт в группе" if ok else "Опрос на сегодня уже был", show_alert=not ok)
        await show(call, menu_text(), menu_kb())
        return

    elif action == "send":
        if not db.is_open(today()):
            await call.answer("Нет открытого опроса", show_alert=True)
            return
        await send_report()
        await call.answer("Заявка отправлена на кухню")
        await show(call, menu_text(), menu_kb())
        return

    elif action == "cancel":
        db.set_setting("skip_day", today())
        await cancel_today()
        await call.answer("Сегодняшний опрос отменён")
        await show(call, menu_text(), menu_kb())
        return

    elif action == "skip":
        day = today()
        db.set_setting("skip_day", "" if setting("skip_day") == day else day)
        await show(call, menu_text(), menu_kb())

    elif action == "pause":
        db.set_setting("enabled", "0" if setting("enabled") == "1" else "1")
        await show(call, menu_text(), menu_kb())

    # --- сотрудники ---
    elif action == "users":
        await show_users(call, int(parts[2]))

    elif action == "user":
        await show(call, user_card(int(parts[2])), user_kb(int(parts[2])))

    elif action == "uname":
        uid = int(parts[2])
        await state.set_state(Input.uname)
        await state.update_data(uid=uid)
        await show(call, f"Сейчас: <b>{escape(db.get_user(uid)['display'])}</b>\n\n"
                         "Отправьте официальные имя и фамилию сотрудника.",
                   kb([("↩️ Сбросить на имя из Telegram", f"adm:unamereset:{uid}")], [("« Назад", f"adm:user:{uid}")]))

    elif action == "unamereset":
        uid = int(parts[2])
        db.set_official_name(uid, None)
        await show(call, user_card(uid), user_kb(uid))

    elif action == "ucash":
        uid = int(parts[2])
        await state.set_state(Input.ucash)
        await state.update_data(uid=uid)
        await show(call, f"{balance_text(uid)}\n\nОтправьте сумму, которую сотрудник отдал (например наличными) — "
                         "она сразу зачтётся. Чтобы добавить долг (исправить ошибку), отправьте сумму с минусом: <b>-25000</b>",
                   kb([("« Назад", f"adm:user:{uid}")]))

    # --- деньги ---
    elif action == "debts":
        p = price()
        if not p:
            await show(call, "Цена обеда не задана — долги не считаются. Задайте её в ⚙️ Настройках.", kb(BACK))
        else:
            rows = [r for r in db.balances(p) if r["debt"] != 0]
            lines = [f"{'🔴' if r['debt'] > 0 else '🟢'} {escape(r['name'])} — "
                     f"{money(r['debt']) if r['debt'] > 0 else 'переплата ' + money(-r['debt'])}"
                     + ("" if r["can_dm"] else " ✉️❌") for r in rows]
            total = sum(r["debt"] for r in rows if r["debt"] > 0)
            text = ("💰 <b>Долги</b>\n\n" + ("\n".join(lines) or "Долгов нет ✅") +
                    f"\n\nВсего долг: <b>{money(total)}</b>\n✉️❌ — не нажимал Start, напоминание не получит")
            await show(call, text, kb([("📣 Напомнить должникам сейчас", "adm:remindnow")], BACK))

    elif action == "pays":
        pending = db.pending_payments()
        if not pending:
            await show(call, "💳 Оплат на проверке нет.", kb(BACK))
        else:
            rows = [[(f"{p['name']} — {money(p['amount'])} — {p['created_at'][5:10]}", f"adm:payopen:{p['id']}")]
                    for p in pending[:30]]
            await show(call, f"💳 <b>Ждут проверки: {len(pending)}</b>\nНажмите, чтобы открыть скриншот.", kb(*rows, BACK))

    elif action == "payopen":
        p = db.get_payment(int(parts[2]))
        if p["status"] != "pending":
            await call.answer("Уже обработана", show_alert=True)
            return
        await send_payment_card(call.message, p)

    # --- меню ---
    elif action == "mweek":
        await show_week(call, date.fromisoformat(parts[2]))

    elif action == "mday":
        day = parts[2]
        await state.set_state(Input.menu)
        await state.update_data(day=day)
        cur = db.get_menu(day)
        await show(call, f"🍲 <b>Меню на {short_day(day)}</b>\n\n" +
                   (f"Сейчас:\n{escape(cur)}\n\n" if cur else "") + "Отправьте текст меню (можно в несколько строк).",
                   kb([("🗑 Очистить", f"adm:mclear:{day}"),
                       ("« К неделе", f"adm:mweek:{week_start(date.fromisoformat(day))}")]))

    elif action == "mclear":
        day = date.fromisoformat(parts[2])
        db.set_menu(day.isoformat(), None)
        await show_week(call, week_start(day))

    elif action == "mcopy":
        monday = date.fromisoformat(parts[2])
        copied = 0
        for i in range(7):
            src = db.get_menu((monday - timedelta(days=7 - i)).isoformat())
            if src:
                db.set_menu((monday + timedelta(days=i)).isoformat(), src)
                copied += 1
        await call.answer(f"Скопировано дней: {copied}" if copied else "На прошлой неделе меню пустое",
                          show_alert=not copied)
        await show_week(call, monday)
        return

    # --- отчёты ---
    elif action == "reports":
        await show(call, "📈 За какой период?", kb(
            [("Этот месяц", "adm:rep:{}:{}".format(*this_month())),
             ("Прошлый месяц", "adm:rep:{}:{}".format(*prev_month()))],
            [("📆 Указать период", "adm:period")],
            BACK))

    elif action == "period":
        await state.set_state(Input.period)
        await show(call, "Отправьте период: <b>01.10.2026 15.10.2026</b>\nили месяц: <b>10.2026</b>",
                   kb([("« Назад", "adm:reports")]))

    elif action == "rep":
        d1, d2 = parts[2], parts[3]
        await show(call, stats_text(d1, d2), report_kb(d1, d2))

    elif action == "xls":
        d1, d2 = parts[2], parts[3]
        await call.answer("Готовлю файл…")
        await call.message.answer_document(
            BufferedInputFile(build_excel(d1, d2), filename=f"obedy_{d1}_{d2}.xlsx"),
            caption=f"📥 Обеды {date.fromisoformat(d1):%d.%m.%Y} – {date.fromisoformat(d2):%d.%m.%Y}")
        return

    await call.answer()


def report_kb(d1: str, d2: str):
    return kb([("📥 Скачать Excel", f"adm:xls:{d1}:{d2}")], [("« К периодам", "adm:reports")], BACK)


# ---------- ввод значений ----------

@router.message(Input.time, F.text)
async def got_time(message: Message, state: FSMContext):
    m = TIME_RE.match(message.text.strip())
    if not m:
        await message.answer("Не понял время. Формат: 10:30")
        return
    value = f"{int(m.group(1)):02d}:{m.group(2)}"
    key = (await state.get_data())["key"]
    times = {k: setting(k) for k in ("poll_time", "remind_time", "report_time")}
    if key in times:
        times[key] = value
        if not times["poll_time"] < times["remind_time"] < times["report_time"]:
            await message.answer(
                f"Должно быть: опрос → напоминание → закрытие.\nПолучится: {times['poll_time']} → "
                f"{times['remind_time']} → {times['report_time']}. Отправьте другое время.")
            return
    db.set_setting(key, value)
    schedule_jobs()
    await state.clear()
    await message.answer(f"✅ Сохранено: {TIME_LABELS[key]} — {value}")
    await message.answer(settings_text(), reply_markup=settings_kb())


@router.message(Input.price, F.text)
async def got_price(message: Message, state: FSMContext):
    value = re.sub(r"[\s_.,]|сум", "", message.text.lower())
    if not value.isdigit():
        await message.answer("Отправьте число, например 25000")
        return
    db.set_setting("price", str(int(value)))
    day = db.get_day(today())
    if day and not day["closed"]:
        db.open_day(day["day"], day["chat_id"], day["message_id"], int(value), day["poll_id"])
    await state.clear()
    await message.answer("✅ Цена сохранена")
    await message.answer(settings_text(), reply_markup=settings_kb())


@router.message(Input.card, F.text)
async def got_card(message: Message, state: FSMContext):
    value = " ".join(message.text.split())[:200]
    db.set_setting("card", "" if value == "-" else value)
    await state.clear()
    await message.answer("✅ Реквизиты сохранены")
    await message.answer(settings_text(), reply_markup=settings_kb())


@router.message(Input.uname, F.text)
async def got_uname(message: Message, state: FSMContext):
    name = " ".join(message.text.split())[:64]
    if len(name) < 2:
        await message.answer("Слишком коротко, отправьте имя и фамилию")
        return
    uid = (await state.get_data())["uid"]
    db.set_official_name(uid, name)
    await state.clear()
    await message.answer(f"✅ Имя изменено: <b>{escape(name)}</b>")
    await message.answer(user_card(uid), reply_markup=user_kb(uid))


@router.message(Input.ucash, F.text)
async def got_ucash(message: Message, state: FSMContext):
    value = re.sub(r"[\s_.,]|сум", "", message.text.lower()).replace("−", "-")
    if not re.fullmatch(r"-?\d+", value) or int(value) == 0:
        await message.answer("Отправьте сумму числом, например 50000 (или -25000, чтобы добавить долг)")
        return
    uid = (await state.get_data())["uid"]
    amount = int(value)
    db.add_payment(uid, amount, "confirmed", decided_by=message.from_user.id,
                   note="наличными" if amount > 0 else "корректировка")
    await state.clear()
    await message.answer(f"✅ Записано: {money(amount)}")
    if amount > 0:
        await send_dm(uid, f"✅ Администратор записал вашу оплату <b>{money(amount)}</b>.\n\n{balance_text(uid)}")
    await message.answer(user_card(uid), reply_markup=user_kb(uid))


@router.message(Input.menu, F.text)
async def got_menu(message: Message, state: FSMContext):
    day = (await state.get_data())["day"]
    db.set_menu(day, message.text.strip()[:1500])
    await state.clear()
    await message.answer(f"✅ Меню на {short_day(day)} сохранено")
    await show_week(message, week_start(date.fromisoformat(day)))


def parse_date(s: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


@router.message(Input.period, F.text)
async def got_period(message: Message, state: FSMContext):
    parts = message.text.replace("–", " ").replace(" - ", " ").split()
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
        await message.answer("Не понял. Пример: 01.10.2026 15.10.2026 или 10.2026")
        return
    await state.clear()
    await message.answer(stats_text(d1, d2), reply_markup=report_kb(d1, d2))
