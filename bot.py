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


async def _products_kb() -> InlineKeyboardMarkup:
    products = await get_products(active_only=False)
    rows = [
        [InlineKeyboardButton(
            text=f"{'✅' if p['is_active'] else '❌'} {p['name_uz']} — {int(p['price']):,} so'm",
            callback_data=f"toggle:{p['id']}",
        )]
        for p in products
    ]
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

@router.message(F.photo)
async def handle_photo(msg: Message, bot: Bot) -> None:
    """User sends payment screenshot after webapp closes."""
    try:
        await bot.forward_message(GROUP_ID, msg.chat.id, msg.message_id)
        log.info("Screenshot forwarded to group from user_id=%s", msg.from_user.id)
    except Exception as e:
        log.error("Forward photo failed: %s", e)


# ── WebApp order ──────────────────────────────────────────────────────────────

@router.message(F.web_app_data)
async def webapp_data(msg: Message, bot: Bot) -> None:
    print("=== WEB_APP_DATA RECEIVED ===", flush=True)
    log.info("web_app_data received: %s", msg.web_app_data.data[:100] if msg.web_app_data else None)
    try:
        data = json.loads(msg.web_app_data.data)
    except (json.JSONDecodeError, AttributeError) as e:
        log.error("Invalid webapp data: %s | error: %s", msg.web_app_data.data, e)
        return
    log.info("Parsed action: %s", data.get("action"))
    if data.get("action") == "order":
        await _handle_order(msg, bot, data)
    else:
        log.warning("Unknown action: %s", data.get("action"))


async def _handle_order(msg: Message, bot: Bot, data: dict) -> None:
    log.info("Order received from user_id=%s data=%s", msg.from_user.id,
             {k: v[:30] if isinstance(v, str) and len(v) > 30 else v
              for k, v in data.items() if k != "screenshot"})

    user = await get_user(msg.from_user.id)
    lang = user["lang"] if user else "uz"
    log.info("User lookup: %s", user)

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
            "screenshot": None,  # screenshot alohida rasm xabari orqali keladi
            "comment":    data.get("comment"),
        })
        log.info("Order saved: order_id=%s", order_id)
    except Exception as e:
        log.error("create_order failed: %s", e)
        return

    await msg.answer(t(lang, "order_received"))
    if data.get("has_screenshot"):
        await msg.answer(t(lang, "send_screenshot"))

    items_lines = "".join(
        f"  • {item['name']} ×{item['qty']} — {item['price'] * item['qty']:,.0f} so'm\n"
        for item in data.get("items", [])
    ) or "  • (bo'sh)\n"

    group_text = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"📍 {data.get('address', '—')}\n\n"
        f"{items_lines}\n"
        f"💰 Jami: <b>{data.get('total', 0):,.0f} so'm</b>\n"
        f"💬 Izoh: {data.get('comment') or '—'}"
    )

    log.info("Sending to GROUP_ID=%s", GROUP_ID)
    try:
        await bot.send_message(GROUP_ID, group_text, parse_mode="HTML")
        log.info("Group message sent OK")
    except Exception as e:
        log.error("send_message failed: %s", e)
        return

    screenshot = data.get("screenshot")
    if screenshot and screenshot.startswith("data:image"):
        try:
            _, b64data = screenshot.split(",", 1)
            from aiogram.types import BufferedInputFile
            photo = BufferedInputFile(base64.b64decode(b64data), filename="screenshot.jpg")
            await bot.send_photo(
                GROUP_ID, photo,
                caption=f"Buyurtma #{order_id} — to'lov skrinshoti",
            )
            log.info("Screenshot sent OK")
        except Exception as e:
            log.error("send_photo failed: %s", e)


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