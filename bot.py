import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from config import ADMIN_IDS, BOT_TOKEN, GROUP_ID, WEBAPP_URL
from database import (
    create_order,
    get_products,
    get_user,
    init_db,
    monthly_report,
    toggle_product,
    upsert_user,
)
from til import t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

router = Router()


class Reg(StatesGroup):
    lang = State()
    name = State()


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _lang_kb(change: bool = False) -> InlineKeyboardMarkup:
    prefix = "clang" if change else "lang"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbek",  callback_data=f"{prefix}:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{prefix}:ru"),
    ]])


async def _products_kb() -> InlineKeyboardMarkup:
    """One row per product: name + toggle button."""
    products = await get_products(active_only=False)
    rows = []
    for p in products:
        status = "✅" if p["is_active"] else "❌"
        rows.append([InlineKeyboardButton(
            text=f"{status} {p['name_uz']} — {int(p['price']):,} so'm",
            callback_data=f"toggle:{p['id']}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Mahsulotlar",       callback_data="admin:products")],
        [InlineKeyboardButton(text="📊 Oylik hisobot",     callback_data="admin:report")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    user = await get_user(msg.from_user.id)
    if user and user.get("full_name"):
        await show_main_menu(msg, user["lang"])
        return
    await state.set_state(Reg.lang)
    await msg.answer(t("uz", "choose_lang"), reply_markup=_lang_kb(change=False))


@router.callback_query(Reg.lang, F.data.startswith("lang:"))
async def cb_lang_reg(cb: CallbackQuery, state: FSMContext) -> None:
    lang = cb.data.split(":")[1]
    await state.update_data(lang=lang)
    await state.set_state(Reg.name)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t(lang, "enter_name"))
    await cb.answer()


@router.message(Reg.name)
async def reg_name(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    full_name = msg.text.strip()
    await upsert_user(msg.from_user.id, full_name=full_name, lang=lang)
    await state.clear()
    await msg.answer(t(lang, "welcome", name=full_name))
    await show_main_menu(msg, lang)


# ── Main menu ─────────────────────────────────────────────────────────────────

async def show_main_menu(msg: Message, lang: str) -> None:
    user_id   = msg.from_user.id
    is_admin  = user_id in ADMIN_IDS
    from config import PAYMENT_CARD, PAYMENT_OWNER
    from urllib.parse import quote
    webapp_url = (
        f"{WEBAPP_URL}/webapp"
        f"?user_id={user_id}&lang={lang}"
        f"&card={quote(PAYMENT_CARD)}&owner={quote(PAYMENT_OWNER)}"
    )

    rows = [
        [KeyboardButton(text=t(lang, "order_btn"), web_app=WebAppInfo(url=webapp_url))],
        [KeyboardButton(text=t(lang, "change_lang"))],
    ]
    if is_admin:
        rows.insert(1, [KeyboardButton(text="⚙️ Admin")])

    kb = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
    await msg.answer(t(lang, "main_menu"), reply_markup=kb)


# ── Language change ───────────────────────────────────────────────────────────

@router.message(F.text.in_(["🌐 Tilni o'zgartirish", "🌐 Сменить язык"]))
async def change_lang(msg: Message, state: FSMContext) -> None:
    await state.set_state(Reg.lang)
    await msg.answer(t("uz", "choose_lang"), reply_markup=_lang_kb(change=True))


@router.callback_query(Reg.lang, F.data.startswith("clang:"))
async def cb_lang_change(cb: CallbackQuery, state: FSMContext) -> None:
    lang = cb.data.split(":")[1]
    await upsert_user(cb.from_user.id, lang=lang)
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await show_main_menu(cb.message, lang)
    await cb.answer()


# ── Admin panel ───────────────────────────────────────────────────────────────

@router.message(F.text == "⚙️ Admin")
async def admin_panel(msg: Message) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await msg.answer("⚙️ <b>Admin panel</b>", parse_mode="HTML", reply_markup=_admin_kb())


@router.callback_query(F.data == "admin:products")
async def cb_admin_products(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    kb = await _products_kb()
    await cb.message.edit_text("📦 <b>Mahsulotlar</b>\nFaolligini o'zgartirish uchun bosing:", parse_mode="HTML", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("toggle:"))
async def cb_toggle(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    product_id = int(cb.data.split(":")[1])
    try:
        new_state = await toggle_product(product_id)
        status = "faollashtirildi ✅" if new_state else "o'chirildi ❌"
        await cb.answer(f"Mahsulot {status}", show_alert=False)
        kb = await _products_kb()
        await cb.message.edit_reply_markup(reply_markup=kb)
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)


@router.callback_query(F.data == "admin:report")
async def cb_admin_report(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    report = await monthly_report()
    text = (
        "📊 <b>Oylik hisobot</b>\n\n"
        f"Buyurtmalar soni: <b>{report['cnt']}</b>\n"
        f"Jami daromad: <b>{report['revenue']:,.0f} so'm</b>"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")]
    ])
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await cb.answer()


@router.callback_query(F.data == "admin:back")
async def cb_admin_back(cb: CallbackQuery) -> None:
    await cb.message.edit_text("⚙️ <b>Admin panel</b>", parse_mode="HTML", reply_markup=_admin_kb())
    await cb.answer()


# ── WebApp order handler ──────────────────────────────────────────────────────

@router.message(F.web_app_data)
async def webapp_data(msg: Message, bot: Bot) -> None:
    try:
        data = json.loads(msg.web_app_data.data)
    except (json.JSONDecodeError, AttributeError):
        log.error("Invalid webapp data: %s", msg.web_app_data.data)
        return

    if data.get("action") == "order":
        await _handle_order(msg, bot, data)


async def _handle_order(msg: Message, bot: Bot, data: dict) -> None:
    user   = await get_user(msg.from_user.id)
    lang   = user["lang"] if user else "uz"

    order_id = await create_order({
        "user_id":    msg.from_user.id,
        "items":      json.dumps(data.get("items", []), ensure_ascii=False),
        "total":      data.get("total", 0),
        "phone":      data.get("phone"),
        "latitude":   data.get("latitude"),
        "longitude":  data.get("longitude"),
        "address":    data.get("address"),
        "duration":   data.get("duration"),
        "order_type": data.get("order_type", "product"),
        "screenshot": data.get("screenshot"),
        "comment":    data.get("comment"),
    })

    await msg.answer(t(lang, "order_received"))

    items_lines = "".join(
        f"  • {item['name']} ×{item['qty']} — {item['price'] * item['qty']:,.0f} so'm\n"
        for item in data.get("items", [])
    ) or "  • Servis xizmati\n"

    group_text = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"📦 Tur: {'Mahsulot' if data.get('order_type') == 'product' else 'Servis'}\n\n"
        f"{items_lines}\n"
        f"💰 Jami: <b>{data.get('total', 0):,.0f} so'm</b>\n"
        f"💬 Izoh: {data.get('comment') or '—'}"
    )

    try:
        await bot.send_message(GROUP_ID, group_text, parse_mode="HTML")
    except Exception as e:
        log.error("Group message failed: %s", e)


# ── Boot ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Bot starting…")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())