"""Личка сотрудника: /start — имя и фамилия, профиль с долгом и обедами, оплата со скриншотом."""
import re
from datetime import date, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core import (MONTHS_IN, balance_text, card_text, db, debt_of, kb, money, notify_admins, price,
                  this_month, today_date, week_menu_text, week_start)

router = Router()
router.message.filter(F.chat.type == "private")

STATUS_ICON = {"pending": "⏳", "confirmed": "✅", "rejected": "❌"}


class U(StatesGroup):
    name = State()
    amount = State()
    proof = State()


PROFILE_KB = kb(
    [("💳 Оплатить", "u:pay"), ("📅 История", "u:hist")],
    [("🍲 Меню на неделю", "u:menu"), ("✏️ Изменить имя", "u:name")],
    [("🔄 Обновить", "u:profile")],
)
BACK_KB = kb([("« Назад", "u:profile")])


def profile_text(user_id: int) -> str:
    u = db.get_user(user_id)
    d1, d2 = this_month()
    lunches = db.user_lunches(user_id, d1, d2)
    dates = ", ".join(f"{date.fromisoformat(r['day']):%d.%m}" + (f" ×{r['portions']}" if r["portions"] > 1 else "")
                      for r in lunches) or "пока не было"
    money_line = balance_text(user_id) if price() else "💰 Цена обеда пока не задана администратором."
    return (f"👤 <b>{escape(u['display'])}</b>\n\n{money_line}\n\n"
            f"📅 Обеды в {MONTHS_IN[today_date().month]}: <b>{len(lunches)}</b>\n{dates}")


async def show_profile(target: Message | CallbackQuery):
    user_id = target.from_user.id
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(profile_text(user_id), reply_markup=PROFILE_KB)
            return
        except Exception:
            target = target.message
    await target.answer(profile_text(user_id), reply_markup=PROFILE_KB)


def ask_name_text(first_time: bool) -> str:
    head = "👋 Здравствуйте! Я веду учёт обедов в столовой.\n\n" if first_time else ""
    return head + ("Напишите, пожалуйста, ваши <b>имя и фамилию</b> так, как в официальных документах — "
                   "по ним ведётся учёт обедов и оплат.\n\nНапример: <i>Иванов Иван</i>")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    db.save_user(u.id, u.full_name, u.username)
    db.set_can_dm(u.id, True)
    if not db.get_user(u.id)["name_asked"]:
        await state.set_state(U.name)
        await message.answer(ask_name_text(first_time=True),
                             reply_markup=kb([("Пропустить — взять имя из Telegram", "u:skipname")]))
        return
    await show_profile(message)


@router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext):
    db.save_user(message.from_user.id, message.from_user.full_name, message.from_user.username)
    await start_payment(message, state)


@router.message(U.name, F.text, ~F.text.startswith("/"))
async def got_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if len(name.split()) < 2 or len(name) > 64 or not re.search(r"[^\W\d_]", name):
        await message.answer("Напишите имя и фамилию через пробел, например: <i>Иванов Иван</i>")
        return
    db.set_official_name(message.from_user.id, name)
    await state.clear()
    await message.answer(f"✅ Записал: <b>{escape(name)}</b>")
    await show_profile(message)


@router.callback_query(F.data.startswith("u:"))
async def on_user_button(call: CallbackQuery, state: FSMContext):
    u = call.from_user
    db.save_user(u.id, u.full_name, u.username)
    action = call.data.split(":")[1]

    if action == "skipname":
        await state.clear()
        db.mark_name_asked(u.id)
        await call.answer("Буду использовать имя из Telegram")
        await show_profile(call)
    elif action == "name":
        await state.set_state(U.name)
        await call.message.edit_text(ask_name_text(first_time=False), reply_markup=BACK_KB)
        await call.answer()
    elif action == "profile":
        await state.clear()
        await show_profile(call)
        await call.answer()
    elif action == "hist":
        await call.message.edit_text(history_text(u.id), reply_markup=BACK_KB)
        await call.answer()
    elif action == "menu":
        monday = week_start(today_date())
        text = week_menu_text(monday)
        if any(db.get_menu((monday + timedelta(days=7 + i)).isoformat()) for i in range(7)):
            text += "\n\n" + week_menu_text(monday + timedelta(days=7))
        await call.message.edit_text(text, reply_markup=BACK_KB)
        await call.answer()
    elif action == "pay":
        await call.answer()
        await start_payment(call.message, state, user_id=u.id)
    elif action == "amt":
        await state.update_data(amount=int(call.data.split(":")[2]))
        await state.set_state(U.proof)
        await call.message.edit_text(proof_prompt(int(call.data.split(":")[2])), reply_markup=BACK_KB)
        await call.answer()


def history_text(user_id: int) -> str:
    since = (today_date() - timedelta(days=60)).isoformat()
    lunches = db.user_lunches(user_id, since, "9999-12-31")
    lines = ["📅 <b>Обеды за последние 2 месяца</b>"]
    lines += [f"{date.fromisoformat(r['day']):%d.%m.%Y}" + (f" — {r['portions']} порц." if r["portions"] > 1 else "")
              for r in reversed(lunches)] or ["—"]
    lines += ["", "💳 <b>Оплаты</b>"]
    lines += [f"{STATUS_ICON[p['status']]} {p['created_at'][:10]} — {money(p['amount'])}"
              + (f" ({escape(p['note'])})" if p["note"] else "")
              for p in db.user_payments(user_id)] or ["—"]
    lines += ["", "⏳ на проверке · ✅ подтверждена · ❌ отклонена"]
    return "\n".join(lines)


# ---------- оплата ----------

async def start_payment(message: Message, state: FSMContext, user_id: int | None = None):
    user_id = user_id or message.from_user.id
    if not price():
        await message.answer("Цена обеда пока не задана — оплачивать нечего.")
        return
    debt, _ = debt_of(user_id)
    await state.set_state(U.amount)
    buttons = [[(f"Весь долг — {money(debt)}", f"u:amt:{debt}")]] if debt > 0 else []
    await message.answer(
        f"{balance_text(user_id)}\n\n{card_text()}\n\n"
        "1️⃣ Переведите деньги\n2️⃣ Напишите сюда <b>сумму</b> перевода (или нажмите кнопку)\n"
        "3️⃣ Пришлите <b>скриншот</b> перевода",
        reply_markup=kb(*buttons, [("« Отмена", "u:profile")]))


def proof_prompt(amount: int) -> str:
    return f"Сумма: <b>{money(amount)}</b>\n\n📸 Теперь пришлите скриншот перевода (фото или файл)."


@router.message(U.amount, F.text, ~F.text.startswith("/"))
async def got_amount(message: Message, state: FSMContext):
    value = re.sub(r"[\s_.,]|сум", "", message.text.lower())
    if not value.isdigit() or int(value) <= 0:
        await message.answer("Напишите сумму числом, например <b>125000</b>")
        return
    await state.update_data(amount=int(value))
    await state.set_state(U.proof)
    await message.answer(proof_prompt(int(value)), reply_markup=BACK_KB)


@router.message(U.proof, F.photo | F.document)
async def got_proof(message: Message, state: FSMContext):
    amount = (await state.get_data())["amount"]
    u = message.from_user
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
    else:
        file_id, file_type = message.document.file_id, "document"
    payment_id = db.add_payment(u.id, amount, "pending", file_id, file_type)
    await state.clear()

    debt, _ = debt_of(u.id)
    user = db.get_user(u.id)
    caption = (f"💳 <b>Оплата за обеды</b>\n\n👤 {escape(user['display'])}"
               + (f" (@{u.username})" if u.username else "")
               + f"\n💵 Сумма: <b>{money(amount)}</b>\n💰 Долг до подтверждения: {money(debt)}")
    buttons = kb([("✅ Подтвердить", f"pay:ok:{payment_id}"), ("❌ Отклонить", f"pay:no:{payment_id}")])
    sent = await notify_admins(caption, reply_markup=buttons,
                               **({"photo": file_id} if file_type == "photo" else {"document": file_id}))
    if sent:
        await message.answer("✅ Скриншот отправлен администратору. Как только он подтвердит оплату, долг обновится.")
    else:
        await message.answer("⚠️ Скриншот сохранён, но администратор пока не открыл бота. "
                             "Оплата будет проверена, когда он зайдёт в панель.")
    await show_profile(message)


@router.message(U.proof)
async def proof_not_photo(message: Message):
    await message.answer("Пришлите, пожалуйста, скриншот перевода — фото или файл.", reply_markup=BACK_KB)
