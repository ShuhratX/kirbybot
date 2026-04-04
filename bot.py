import asyncio
import base64
import json
import logging
from urllib.parse import quote

import aiohttp
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
from config import (
    ADMIN_IDS, BOT_TOKEN, GITHUB_BRANCH, GITHUB_FILE,
    GITHUB_REPO, GITHUB_TOKEN, GROUP_ID, PAYMENT_CARD,
    PAYMENT_OWNER, WEBAPP_URL,
)
from database import (
    create_order, get_products, get_user,
    init_db, monthly_report, toggle_product, upsert_user,
)
from til import t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

router = Router()


class Reg(StatesGroup):
    lang = State()
    name = State()


class Order(StatesGroup):
    waiting_screenshot = State()


# ── GitHub Pages push ─────────────────────────────────────────────────────────

async def push_products_json() -> bool:
    """
    Fetch active products from DB and push products.json to GitHub Pages branch.
    Returns True on success, False on failure.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GitHub credentials not configured — skipping push")
        return False

    products = await get_products(active_only=True)
    content  = json.dumps(products, ensure_ascii=False, indent=2)
    b64      = base64.b64encode(content.encode()).decode()

    api_url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers  = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with aiohttp.ClientSession() as session:
        # Get current SHA (needed for update)
        sha = None
        async with session.get(
            api_url, headers=headers, params={"ref": GITHUB_BRANCH}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha  = data.get("sha")

        payload: dict = {
            "message": "chore: update products.json",
            "content": b64,
            "branch":  GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        async with session.put(api_url, headers=headers, json=payload) as resp:
            if resp.status in (200, 201):
                log.info("products.json pushed to GitHub Pages")
                return True
            text = await resp.text()
            log.error("GitHub push failed %s: %s", resp.status, text)
            return False


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _lang_kb(change: bool = False) -> InlineKeyboardMarkup:
    prefix = "clang" if change else "lang"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbek",  callback_data=f"{prefix}:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{prefix}:ru"),
    ]])


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Mahsulotlar",   callback_data="admin:products")],
        [InlineKeyboardButton(text="📊 Oylik hisobot", callback_data="admin:report")],
    ])


async def _products_kb(lang: str, active_only: bool = False):  # Default False turaversin
    # Bazadan hammasini olamiz
    products = await get_products()

    # AGAR mijoz uchun bo'lsa (active_only=True bo'lib kelsa), filtrlaymiz
    if active_only:
        products = [p for p in products if p.get("is_active") == 1]

    if not products:
        return None

    products_json = json.dumps(products)
    encoded_products = quote(products_json)

    # WebApp URL endi faqat filtrgan mahsulotlarni o'z ichiga oladi
    webapp_url_with_data = f"{WEBAPP_URL}?products={encoded_products}"

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "buyurtma_berish"), web_app=WebAppInfo(url=webapp_url_with_data))],
            [KeyboardButton(text=t(lang, "back"))]
        ],
        resize_keyboard=True
    )


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
    data      = await state.get_data()
    lang      = data.get("lang", "uz")
    full_name = msg.text.strip()
    await upsert_user(msg.from_user.id, full_name=full_name, lang=lang)
    await state.clear()
    await msg.answer(t(lang, "welcome", name=full_name))
    await show_main_menu(msg, lang)


# ── Main menu ─────────────────────────────────────────────────────────────────

async def show_main_menu(msg: Message, lang: str) -> None:
    user_id  = msg.from_user.id
    is_admin = user_id in ADMIN_IDS

    webapp_url = (
        f"{WEBAPP_URL}"
        f"?lang={lang}"
        f"&card={quote(PAYMENT_CARD)}"
        f"&owner={quote(PAYMENT_OWNER)}"
    )

    rows = [
        [KeyboardButton(text=t(lang, "order_btn"), web_app=WebAppInfo(url=webapp_url))],
        [KeyboardButton(text=t(lang, "change_lang"))],
    ]
    if is_admin:
        rows.insert(1, [KeyboardButton(text="⚙️ Admin")])

    await msg.answer(
        t(lang, "main_menu"),
        reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True),
    )


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
        await cb.answer("Ruxsat yo'q", show_alert=True); return
    await cb.message.edit_text(
        "📦 <b>Mahsulotlar</b>\nFaolligini o'zgartirish uchun bosing:",
        parse_mode="HTML",
        reply_markup=await _products_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("toggle:"))
async def cb_toggle(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True); return

    product_id = int(cb.data.split(":")[1])
    try:
        new_state = await toggle_product(product_id)
    except ValueError as e:
        await cb.answer(str(e), show_alert=True); return

    # Push updated products.json to GitHub Pages
    ok = await push_products_json()
    status_txt = "faollashtirildi ✅" if new_state else "o'chirildi ❌"
    push_txt   = "" if ok else " (GitHub push muvaffaqiyatsiz ⚠️)"
    await cb.answer(f"Mahsulot {status_txt}{push_txt}")
    await cb.message.edit_reply_markup(reply_markup=await _products_kb())


@router.callback_query(F.data == "admin:report")
async def cb_admin_report(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True); return
    report = await monthly_report()
    await cb.message.edit_text(
        f"📊 <b>Oylik hisobot</b>\n\n"
        f"Buyurtmalar soni: <b>{report['cnt']}</b>\n"
        f"Jami daromad: <b>{report['revenue']:,.0f} so'm</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")]
        ]),
    )
    await cb.answer()


@router.callback_query(F.data == "admin:back")
async def cb_admin_back(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "⚙️ <b>Admin panel</b>", parse_mode="HTML", reply_markup=_admin_kb()
    )
    await cb.answer()


# ── Screenshot handler ───────────────────────────────────────────────────────

@router.message(Order.waiting_screenshot, F.photo)
async def handle_screenshot(msg: Message, bot: Bot, state: FSMContext) -> None:
    state_data = await state.get_data()
    data       = state_data.get("order_data", {})
    lang       = state_data.get("order_lang", "uz")
    await state.clear()

    user = await get_user(msg.from_user.id)

    try:
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
            "screenshot": None,
            "comment":    data.get("comment"),
        })
        log.info("Order saved: order_id=%s", order_id)
    except Exception as e:
        log.error("create_order failed: %s", e)
        return

    items_lines = "".join(
        f"  • {item['name']} ×{item['qty']} — {item['price'] * item['qty']:,.0f} so'm\n"
        for item in data.get("items", [])
    ) or "  • (bo'sh)\n"

    caption = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"{items_lines}"
        f"💰 Jami: <b>{data.get('total', 0):,.0f} so'm</b>\n"
        f"💬 Izoh: {data.get('comment') or '—'}"
    )

    targets = list({GROUP_ID, *ADMIN_IDS})
    targets = list({GROUP_ID, *ADMIN_IDS})
    for target in targets:
        try:
            # Avval foto va caption yuboriladi
            await bot.send_photo(
                target,
                msg.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML"
            )

            # Keyin nativ lokatsiya yuboriladi
            if data.get("latitude") and data.get("longitude"):
                await bot.send_location(
                    chat_id=target,
                    latitude=float(data["latitude"]),
                    longitude=float(data["longitude"])
                )
        except Exception as e:
            log.error("send_photo/location to %s failed: %s", target, e)

    await msg.answer(t(lang, "order_received"))


# ── WebApp order ──────────────────────────────────────────────────────────────

@router.message(F.web_app_data)
async def webapp_data(msg: Message, bot: Bot, state: FSMContext) -> None:
    try:
        data = json.loads(msg.web_app_data.data)
    except (json.JSONDecodeError, AttributeError):
        log.error("Invalid webapp data: %s", msg.web_app_data.data)
        return
    if data.get("action") == "order":
        if data.get("order_type") == "texosmotr":
            await _handle_texosmotr(msg, bot, data)
        else:
            await _handle_order(msg, data, state)


async def _handle_texosmotr(msg: Message, bot: Bot, data: dict) -> None:
    """Texosmotr: screenshot yo'q, darhol DB ga saqlash va yuborish."""
    log.info("Texosmotr order from user_id=%s", msg.from_user.id)
    user = await get_user(msg.from_user.id)
    lang = user["lang"] if user else "uz"

    try:
        order_id = await create_order({
            "user_id":    msg.from_user.id,
            "items":      "[]",
            "total":      0,
            "phone":      data.get("phone"),
            "latitude":   data.get("latitude"),
            "longitude":  data.get("longitude"),
            "address":    data.get("address"),
            "duration":   data.get("duration"),
            "order_type": "texosmotr",
            "screenshot": None,
            "comment":    data.get("comment"),
        })
        log.info("Texosmotr order saved: order_id=%s", order_id)
    except Exception as e:
        log.error("create_order texosmotr failed: %s", e)
        return

    caption = (
        f"🛠 <b>Texosmotr buyurtmasi #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"📅 Muddat: {data.get('duration', '—')}"
    )

    targets = list({GROUP_ID, *ADMIN_IDS})
    for target in targets:
        try:
            # Ma'lumotlar xabari
            await bot.send_message(target, caption, parse_mode="HTML")

            # Nativ lokatsiya
            if data.get("latitude") and data.get("longitude"):
                await bot.send_location(
                    chat_id=target,
                    latitude=float(data["latitude"]),
                    longitude=float(data["longitude"])
                )
        except Exception as e:
            log.error("send_message/location texosmotr to %s failed: %s", target, e)

    await msg.answer(t(lang, "order_received"))


async def _handle_order(msg: Message, data: dict, state: FSMContext) -> None:
    log.info("Order received from user_id=%s", msg.from_user.id)
    user = await get_user(msg.from_user.id)
    lang = user["lang"] if user else "uz"

    # Buyurtmani vaqtincha state ga saqlaymiz — skrinshot kelgandan keyin DB ga yozamiz
    await state.update_data(order_data=data, order_lang=lang)
    await state.set_state(Order.waiting_screenshot)
    await msg.answer(t(lang, "send_screenshot"))


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