import asyncio
import base64
import json
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from config import ADMIN_IDS, BOT_TOKEN, GROUP_ID, WEBAPP_URL
from database import create_order, get_user, init_db, monthly_report, toggle_product, upsert_user
from til import t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

router = Router()


class Reg(StatesGroup):
    lang = State()
    name = State()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lang_kb(change: bool = False) -> InlineKeyboardMarkup:
    """Build language keyboard.
    change=True  → 'clang:' prefix (language-change flow)
    change=False → 'lang:'  prefix (registration flow)
    Separating prefixes prevents the duplicate-handler collision bug.
    """
    prefix = "clang" if change else "lang"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data=f"{prefix}:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{prefix}:ru"),
    ]])


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    user = await get_user(msg.from_user.id)
    if user and user.get("full_name"):
        await show_main_menu(msg, user["lang"])
        return
    await state.set_state(Reg.lang)
    await msg.answer(t("uz","choose lang"), reply_markup=_lang_kb(change=False))


# FIX #1: Registration flow uses 'lang:' prefix — distinct from change flow
@router.callback_query(Reg.lang, F.data.startswith("lang:"))
async def cb_lang_registration(cb: CallbackQuery, state: FSMContext) -> None:
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
    user_id = msg.from_user.id
    is_admin = user_id in ADMIN_IDS

    webapp_url = f"{WEBAPP_URL}/webapp?user_id={user_id}&lang={lang}"
    admin_url  = f"{WEBAPP_URL}/admin?user_id={user_id}"

    rows = [
        [KeyboardButton(text=t(lang, "order_btn"), web_app=WebAppInfo(url=webapp_url))],
        [KeyboardButton(text=t(lang, "change_lang"))],
    ]
    if is_admin:
        rows.insert(1, [KeyboardButton(text="⚙️ Admin panel", web_app=WebAppInfo(url=admin_url))])

    kb = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
    await msg.answer(t(lang, "main_menu"), reply_markup=kb)


# ── Language change (separate 'clang:' prefix) ────────────────────────────────

@router.message(F.text.in_(["🌐 Tilni o'zgartirish", "🌐 Сменить язык"]))
async def change_lang(msg: Message, state: FSMContext) -> None:
    await state.set_state(Reg.lang)
    # FIX #1: change=True sends 'clang:' prefix to hit cb_lang_change, not cb_lang_registration
    await msg.answer(t("uz", "choose_lang"), reply_markup=_lang_kb(change=True))


@router.callback_query(Reg.lang, F.data.startswith("clang:"))
async def cb_lang_change(cb: CallbackQuery, state: FSMContext) -> None:
    lang = cb.data.split(":")[1]
    await upsert_user(cb.from_user.id, lang=lang)
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await show_main_menu(cb.message, lang)
    await cb.answer()


# ── WebApp data handler ───────────────────────────────────────────────────────

@router.message(F.web_app_data)
async def webapp_data(msg: Message, bot: Bot) -> None:
    try:
        data = json.loads(msg.web_app_data.data)
    except json.JSONDecodeError:
        log.error("Invalid webapp data: %s", msg.web_app_data.data)
        return

    action = data.get("action")

    if action == "order":
        await handle_order(msg, bot, data)
    elif action == "toggle_product":
        if msg.from_user.id in ADMIN_IDS:
            new_state = await toggle_product(data["product_id"])
            status = "faollashtirildi ✅" if new_state else "o'chirildi ❌"
            await msg.answer(f"Mahsulot {status}")
    elif action == "monthly_report":
        if msg.from_user.id in ADMIN_IDS:
            report = await monthly_report()
            await msg.answer(
                f"📊 <b>Oylik hisobot</b>\n\n"
                f"Buyurtmalar soni: <b>{report['cnt']}</b>\n"
                f"Jami daromad: <b>{report['revenue']:,.0f} so'm</b>",
                parse_mode="HTML",
            )


async def handle_order(msg: Message, bot: Bot, data: dict) -> None:
    user = await get_user(msg.from_user.id)
    lang = user["lang"] if user else "uz"

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

    order_type = data.get("order_type", "product")
    items_text = ""
    if order_type == "product":
        for item in data.get("items", []):
            items_text += f"  • {item['name']} x{item['qty']} — {item['price'] * item['qty']:,.0f} so'm\n"
    else:
        items_text = "  • Servis xizmati\n"

    group_msg = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"📍 {data.get('address', '—')}\n"
        f"⏱ Muddat: {data.get('duration', '—')}\n"
        f"📦 Tur: {'Mahsulot' if order_type == 'product' else 'Servis'}\n\n"
        f"{items_text}\n"
        f"💰 Jami: <b>{data.get('total', 0):,.0f} so'm</b>\n"
        f"💬 Izoh: {data.get('comment') or '—'}"
    )

    try:
        await bot.send_message(GROUP_ID, group_msg, parse_mode="HTML")

        # FIX #4: Handle base64 vs URL vs relative path correctly
        screenshot = data.get("screenshot")
        if screenshot:
            if screenshot.startswith("data:image"):
                _, b64data = screenshot.split(",", 1)
                img_bytes = base64.b64decode(b64data)
                photo = BufferedInputFile(img_bytes, filename="screenshot.jpg")
            elif screenshot.startswith("http"):
                photo = screenshot
            else:
                photo = f"{WEBAPP_URL}{screenshot}"

            await bot.send_photo(
                GROUP_ID, photo,
                caption=f"Buyurtma #{order_id} — to'lov skrinshoti",
            )
    except Exception as e:
        log.error("Group send failed: %s", e)


# ── Boot ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Bot starting...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
