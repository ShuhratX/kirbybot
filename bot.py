import asyncio
import base64
import json
import logging
import re
from datetime import date as Date
from urllib.parse import quote

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
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
    WebAppInfo, BufferedInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    ADMIN_IDS, BOT_TOKEN, GITHUB_BRANCH, GITHUB_FILE,
    GITHUB_REPO, GITHUB_TOKEN, GROUP_ID, PAYMENT_CARD,
    PAYMENT_OWNER, WEBAPP_URL, API_KEY,
)
from database import (
    create_order, get_products, get_user,
    init_db, monthly_report, toggle_product, upsert_user, report_by_range,
    get_product_by_id, create_product, update_product_field, delete_product,
)
from report_excel import build_report
from til import t

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

router = Router()


class Reg(StatesGroup):
    lang = State()
    name = State()


class Order(StatesGroup):
    waiting_screenshot = State()


class AdminReport(StatesGroup):
    date_range = State()

class AdminProduct(StatesGroup):
    """Mahsulot qo'shish va tahrirlash uchun FSM."""
    name_uz   = State()
    name_ru   = State()
    desc_uz   = State()
    desc_ru   = State()
    price     = State()
    image_url = State()
    # tahrirlash rejimi uchun — qaysi field o'zgartirilayapti
    edit_field = State()

async def _upload_to_imgbb(bot: Bot, file_id: str) -> str | None:
    """Telegram file_id → ImgBB upload → Direct URL"""

    try:
        # 1. Telegramdan faylni download qilish
        file = await bot.get_file(file_id)
        file_path = file.file_path
        destination = await bot.download_file(file_path)

        # 2. ImgBB API ga yuborish
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', destination.read(), filename='product.jpg')

            url = f"https://api.imgbb.com/1/upload?key={API_KEY}"
            async with session.post(url, data=data) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    return res_json['data']['url']  # To'g'ridan-to'g'ri rasm linki
                else:
                    log.error("ImgBB error: %s", await resp.text())
                    return None
    except Exception as e:
        log.error("ImgBB upload exception: %s", e)
        return None


# ── GitHub Pages push ─────────────────────────────────────────────────────────

async def push_products_json() -> bool:
    """
    Fetch active products from DB and push products.json to GitHub Pages branch.
    Returns True on success, False on failure.
    """
    products = await get_products(active_only=True)
    content = json.dumps(products, ensure_ascii=False, indent=2)

    # 1. Lokal fayl
    try:
        with open("products.json", "w", encoding="utf-8") as f:
            f.write(content)
        log.info("products.json updated locally")
    except Exception as e:
        log.error("Local products.json write failed: %s", e)

    # 2. GitHub (agar credentials bo'lsa)
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.warning("GitHub credentials not configured — skipping push")
        return True  # lokal muvaffaqiyatli bo'lsa yetarli

    b64 = base64.b64encode(content.encode()).decode()
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with aiohttp.ClientSession() as session:
            sha = None
            async with session.get(
                    api_url, headers=headers, params={"ref": GITHUB_BRANCH}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sha = data.get("sha")

            payload: dict = {
                "message": "chore: update products.json",
                "content": b64,
                "branch": GITHUB_BRANCH,
            }
            if sha:
                payload["sha"] = sha

            async with session.put(api_url, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    log.info("products.json pushed to GitHub Pages")
                else:
                    body = await resp.text()
                    log.error("GitHub push failed: %s | %s", resp.status, body)
    except Exception as e:
        log.error("GitHub push exception: %s", e)

    return True


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


async def _products_kb(lang: str) -> InlineKeyboardMarkup:
    products = await get_products(active_only=False)
    builder = InlineKeyboardBuilder()
    for p in products:
        name = p["name_uz"] if lang == "uz" else p["name_ru"]
        status = "✅" if p["is_active"] else "❌"
        builder.button(
            text=f"{status} {name}",
            callback_data=f"toggle_product:{p['id']}"
        )
    builder.adjust(1)
    return builder.as_markup()


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
    user_id = msg.from_user.id
    is_admin = user_id in ADMIN_IDS

    # DB dan faqat aktiv mahsulotlarni ol
    products = await get_products(active_only=True)
    products_b64 = base64.b64encode(
        json.dumps(products, ensure_ascii=False).encode()
    ).decode()

    webapp_url = (
        f"{WEBAPP_URL}"
        f"?lang={lang}"
        f"&card={quote(PAYMENT_CARD)}"
        f"&owner={quote(PAYMENT_OWNER)}"
        f"&products={quote(products_b64)}"
    )

    rows = [
        [KeyboardButton(text=t(lang, "order_btn"), web_app=WebAppInfo(url=webapp_url))],
        [KeyboardButton(text=t(lang, "change_lang"))],
    ]
    if is_admin:
        rows.pop(0)
        rows.insert(0, [KeyboardButton(text="⚙️ Admin")])


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


@router.callback_query(F.data.startswith("toggle_product:"))
async def cb_toggle(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True); return

    product_id = int(cb.data.split(":")[1])
    try:
        new_state = await toggle_product(product_id)
    except ValueError as e:
        await cb.answer(str(e), show_alert=True); return

    # Push updated products.json to GitHub Pages
    user = await get_user(cb.from_user.id)
    lang = user["lang"] if user else "uz"
    ok = await push_products_json()
    status_txt = "faollashtirildi ✅" if new_state else "o'chirildi ❌"
    push_txt   = "" if ok else " (GitHub push muvaffaqiyatsiz ⚠️)"
    await cb.answer(f"Mahsulot {status_txt}{push_txt}")
    await cb.message.edit_reply_markup(reply_markup=await _products_kb(lang=lang))


@router.callback_query(F.data == "admin:report")
async def cb_admin_report(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True);
        return

    today = Date.today()
    month_start = today.replace(day=1).isoformat()  # "2025-07-01"
    month_end = today.isoformat()

    await cb.message.edit_text(
        "📊 <b>Hisobot davri</b>\n\n"
        "Joriy oyni yoki o'z sanangizni tanlang:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📅 Joriy oy ({month_start[:7]})",
                callback_data=f"report_range:{month_start}:{month_end}"
            )],
            [InlineKeyboardButton(
                text="✏️ Boshqa sana kiritish",
                callback_data="report_custom"
            )],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")],
        ])
    )
    await cb.answer()


@router.callback_query(F.data.startswith("report_range:"))
async def cb_report_range(cb: CallbackQuery) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True);
        return

    _, date_from, date_to = cb.data.split(":")
    await cb.answer("Hisobot tayyorlanmoqda...")

    rows = await report_by_range(date_from, date_to)
    if not rows:
        await cb.message.answer("❌ Bu davr uchun buyurtma topilmadi.")
        return

    xlsx_bytes = build_report(rows, date_from, date_to)
    file = BufferedInputFile(xlsx_bytes, filename=f"hisobot_{date_from}_{date_to}.xlsx")
    await cb.message.answer_document(file, caption=f"📊 {date_from} — {date_to} hisoboti\nJami: {len(rows)} ta buyurtma")


@router.callback_query(F.data == "report_custom")
async def cb_report_custom(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True); return
    await state.set_state(AdminReport.date_range)
    await cb.message.answer(
        "📅 Boshlang'ich va tugash sanasini kiriting:\n\n"
        "<code>01.03.2026 - 30.03.2026</code>",
        parse_mode="HTML"
    )
    await cb.answer()


@router.message(AdminReport.date_range)
async def get_date_range(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    parsed = parse_date_range(msg.text or "")
    if not parsed:
        await msg.answer(
            "❌ Format noto'g'ri. Quyidagicha kiriting:\n\n"
            "<code>01.03.2026 30.03.2026</code>",
            parse_mode="HTML"
        )
        return

    date_from, date_to = parsed
    await state.clear()

    rows = await report_by_range(date_from, date_to)
    if not rows:
        await msg.answer("❌ Bu davr uchun buyurtma topilmadi.")
        return

    xlsx_bytes = build_report(rows, date_from, date_to)
    file = BufferedInputFile(xlsx_bytes, filename=f"hisobot_{date_from}_{date_to}.xlsx")
    await msg.answer_document(
        file,
        caption=f"📊 {date_from} — {date_to} hisoboti\nJami: {len(rows)} ta buyurtma",
    )


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
        f"  • {item['name']} × {item['qty']} — {item['price'] * item['qty']:,.0f} so'm\n"
        for item in data.get("items", [])
    ) or "  • (bo'sh)\n"

    caption = (
        f"🆕 <b>Yangi buyurtma #{order_id}</b>\n\n"
        f"👤 {user['full_name'] if user else 'N/A'}\n"
        f"📞 {data.get('phone', '—')}\n"
        f"{items_lines}"
        f"💰 Jami: <b>{data.get('total', 0):,.0f} so'm</b>\n"
        f"📅 Muddat: {data.get('duration', '—')}\n"
        f"💬 Izoh: {data.get('comment') or '—'}"
    )

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
    PICKUP_LAT = 41.336943
    PICKUP_LON = 69.322792
    PICKUP_DURATIONS = {"5 kun"}  # "O'zi olib ketish" variantlari

    if data.get("duration") in PICKUP_DURATIONS:
        try:
            await bot.send_location(
                chat_id=msg.from_user.id,
                latitude=PICKUP_LAT,
                longitude=PICKUP_LON,
            )
            await bot.send_message(
                chat_id=msg.from_user.id,
                text="📍 Do'konimizning manzili — yuqoridagi lokatsiya. Buyurtmangizni shu joydan olib ketishingiz mumkin.",
            )
        except Exception as e:
            log.error("Pickup location send failed: %s", e)
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


def parse_date_range(text: str) -> tuple[str, str] | None:
    text = text.strip()

    # dd.mm.yyyy — dd.mm.yyyy  (tire yoki vergul separator)
    dot = re.findall(r'\d{2}\.\d{2}\.\d{4}', text)
    if len(dot) == 2:
        try:
            d1 = Date(int(dot[0][6:]), int(dot[0][3:5]), int(dot[0][:2]))
            d2 = Date(int(dot[1][6:]), int(dot[1][3:5]), int(dot[1][:2]))
            return d1.isoformat(), d2.isoformat()
        except ValueError:
            pass

    return None


# ── 3. CALLBACK DATA FACTORIES ────────────────────────────────────

class ProdCB(CallbackData, prefix="prod"):
    """Mahsulot ro'yxati va boshqaruv uchun."""
    action: str          # list | add | edit | del | confirm_del | toggle
    pid: int = 0         # product id (0 = yangi)


class ProdEditCB(CallbackData, prefix="pedit"):
    """Qaysi fieldni tahrirlash tanlovi."""
    pid: int
    field: str           # name_uz | name_ru | desc_uz | desc_ru | price | image_url


# ── 4. KEYBOARD HELPERS ───────────────────────────────────────────

async def _products_manage_kb() -> InlineKeyboardMarkup:
    """Admin: mahsulotlar ro'yxati — toggle + tahrirlash + o'chirish."""
    products = await get_products(active_only=False)
    builder = InlineKeyboardBuilder()
    for p in products:
        status = "✅" if p["is_active"] else "❌"
        builder.button(
            text=f"{status} {p['name_uz']}",
            callback_data=ProdCB(action="edit", pid=p["id"]).pack(),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="➕ Yangi mahsulot qo'shish",
            callback_data=ProdCB(action="add", pid=0).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin:back")
    )
    return builder.as_markup()


def _product_detail_kb(pid: int, is_active: bool) -> InlineKeyboardMarkup:
    """Bitta mahsulot uchun: toggle, tahrirlash fieldlari, o'chirish."""
    toggle_text = "❌ O'chirish" if is_active else "✅ Faollashtirish"
    fields = [
        ("name_uz",   "📝 Nomi (UZ)"),
        ("name_ru",   "📝 Nomi (RU)"),
        ("desc_uz",   "📄 Tavsif (UZ)"),
        ("desc_ru",   "📄 Tavsif (RU)"),
        ("price",     "💰 Narx"),
        ("image_url", "🖼 Rasm URL"),
    ]
    builder = InlineKeyboardBuilder()
    for field, label in fields:
        builder.button(
            text=label,
            callback_data=ProdEditCB(pid=pid, field=field).pack(),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=ProdCB(action="toggle", pid=pid).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 O'chirish",
            callback_data=ProdCB(action="del", pid=pid).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Ro'yxatga",
            callback_data=ProdCB(action="list", pid=0).pack(),
        )
    )
    return builder.as_markup()


def _confirm_delete_kb(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Ha, o'chir",
                callback_data=ProdCB(action="confirm_del", pid=pid).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Bekor",
                callback_data=ProdCB(action="edit", pid=pid).pack(),
            ),
        ]
    ])


# ── 5. _admin_kb() ni yangilang ───────────────────────────────────
# Mavjud _admin_kb() funksiyasini quyidagi bilan almashtiring:

def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Mahsulotlar",   callback_data="admin:products")],
        [InlineKeyboardButton(text="📊 Oylik hisobot", callback_data="admin:report")],
    ])

# ESLATMA: admin:products callback endi _products_manage_kb() ni ko'rsatadi


# ── 6. ADMIN PRODUCT HANDLERS ─────────────────────────────────────

# --- Ro'yxat ko'rish ---

@router.callback_query(F.data == "admin:products")
async def cb_admin_products(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.clear()  # har qanday qolgan state ni tozala
    await cb.message.edit_text(
        "📦 <b>Mahsulotlar boshqaruvi</b>\nMahsulotni tanlang yoki yangi qo'shing:",
        parse_mode="HTML",
        reply_markup=await _products_manage_kb(),
    )
    await cb.answer()


@router.callback_query(ProdCB.filter(F.action == "list"))
async def cb_prod_list(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.clear()
    await cb.message.edit_text(
        "📦 <b>Mahsulotlar boshqaruvi</b>\nMahsulotni tanlang yoki yangi qo'shing:",
        parse_mode="HTML",
        reply_markup=await _products_manage_kb(),
    )
    await cb.answer()


# --- Mahsulot detail (tahrirlash sahifasi) ---

@router.callback_query(ProdCB.filter(F.action == "edit"))
async def cb_prod_edit(cb: CallbackQuery, callback_data: ProdCB, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.clear()
    product = await get_product_by_id(callback_data.pid)
    if not product:
        await cb.answer("Mahsulot topilmadi", show_alert=True)
        return

    text = (
        f"📦 <b>{product['name_uz']} / {product['name_ru']}</b>\n\n"
        f"📄 <i>{product['desc_uz']}</i>\n"
        f"💰 Narx: <b>{product['price']:,.0f} so'm</b>\n"
        f"🖼 Rasm: {product['image_url'] or '—'}\n"
        f"{'✅ Faol' if product['is_active'] else '❌ Nofaol'}"
    )
    await cb.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_product_detail_kb(product["id"], bool(product["is_active"])),
    )
    await cb.answer()


# --- Toggle (faollik) ---

@router.callback_query(ProdCB.filter(F.action == "toggle"))
async def cb_prod_toggle(cb: CallbackQuery, callback_data: ProdCB) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    try:
        new_state = await toggle_product(callback_data.pid)
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return

    await push_products_json()
    status_txt = "faollashtirildi ✅" if new_state else "o'chirildi ❌"
    await cb.answer(f"Mahsulot {status_txt}")

    # Detail sahifasini yangilash
    product = await get_product_by_id(callback_data.pid)
    if product:
        text = (
            f"📦 <b>{product['name_uz']} / {product['name_ru']}</b>\n\n"
            f"📄 <i>{product['desc_uz']}</i>\n"
            f"💰 Narx: <b>{product['price']:,.0f} so'm</b>\n"
            f"🖼 Rasm: {product['image_url'] or '—'}\n"
            f"{'✅ Faol' if product['is_active'] else '❌ Nofaol'}"
        )
        await cb.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=_product_detail_kb(product["id"], bool(product["is_active"])),
        )


# --- O'chirish tasdiqlash ---

@router.callback_query(ProdCB.filter(F.action == "del"))
async def cb_prod_del(cb: CallbackQuery, callback_data: ProdCB) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    product = await get_product_by_id(callback_data.pid)
    if not product:
        await cb.answer("Mahsulot topilmadi", show_alert=True)
        return
    await cb.message.edit_text(
        f"🗑 <b>{product['name_uz']}</b> ni o'chirishni tasdiqlaysizmi?\n"
        f"Bu amal qaytarib bo'lmaydi!",
        parse_mode="HTML",
        reply_markup=_confirm_delete_kb(callback_data.pid),
    )
    await cb.answer()


@router.callback_query(ProdCB.filter(F.action == "confirm_del"))
async def cb_prod_confirm_del(cb: CallbackQuery, callback_data: ProdCB) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await delete_product(callback_data.pid)
    await push_products_json()
    await cb.answer("Mahsulot o'chirildi ✅")
    await cb.message.edit_text(
        "📦 <b>Mahsulotlar boshqaruvi</b>\nMahsulotni tanlang yoki yangi qo'shing:",
        parse_mode="HTML",
        reply_markup=await _products_manage_kb(),
    )


# ── 7. YANGI MAHSULOT QO'SHISH FSM ───────────────────────────────

_ADD_STEPS: list[tuple[str, str, str]] = [
    # (state_name,   so'rov matni,                         field_key)
    ("name_uz",   "📝 Mahsulot nomini <b>UZ</b> da kiriting:",   "name_uz"),
    ("name_ru",   "📝 Mahsulot nomini <b>RU</b> da kiriting:",   "name_ru"),
    ("desc_uz",   "📄 Tavsifni <b>UZ</b> da kiriting:",          "desc_uz"),
    ("desc_ru",   "📄 Tavsifni <b>RU</b> da kiriting:",          "desc_ru"),
    ("price",     "💰 Narxni kiriting (faqat raqam, so'mda):",    "price"),
    ("image_url", "🖼 Rasm URL ni kiriting (yoki — yuboring):",   "image_url"),
]

_CANCEL_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="❌ Bekor qilish", callback_data=ProdCB(action="list", pid=0).pack())
]])


@router.callback_query(ProdCB.filter(F.action == "add"))
async def cb_prod_add(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminProduct.name_uz)
    await state.update_data(_mode="add")
    await cb.message.answer(
        "➕ <b>Yangi mahsulot qo'shish</b>\n\n"
        "📝 Mahsulot nomini <b>UZ</b> da kiriting:",
        parse_mode="HTML",
        reply_markup=_CANCEL_KB,
    )
    await cb.answer()


@router.message(AdminProduct.name_uz)
async def add_name_uz(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(name_uz=msg.text.strip())
    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.set_state(AdminProduct.name_ru)
        await msg.answer("📝 Mahsulot nomini <b>RU</b> da kiriting:", parse_mode="HTML", reply_markup=_CANCEL_KB)
    else:
        await _finish_field_edit(msg, state, "name_uz", msg.text.strip())


@router.message(AdminProduct.name_ru)
async def add_name_ru(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(name_ru=msg.text.strip())
    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.set_state(AdminProduct.desc_uz)
        await msg.answer("📄 Tavsifni <b>UZ</b> da kiriting:", parse_mode="HTML", reply_markup=_CANCEL_KB)
    else:
        await _finish_field_edit(msg, state, "name_ru", msg.text.strip())


@router.message(AdminProduct.desc_uz)
async def add_desc_uz(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(desc_uz=msg.text.strip())
    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.set_state(AdminProduct.desc_ru)
        await msg.answer("📄 Tavsifni <b>RU</b> da kiriting:", parse_mode="HTML", reply_markup=_CANCEL_KB)
    else:
        await _finish_field_edit(msg, state, "desc_uz", msg.text.strip())


@router.message(AdminProduct.desc_ru)
async def add_desc_ru(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(desc_ru=msg.text.strip())
    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.set_state(AdminProduct.price)
        await msg.answer("💰 Narxni kiriting (faqat raqam, so'mda):", reply_markup=_CANCEL_KB)
    else:
        await _finish_field_edit(msg, state, "desc_ru", msg.text.strip())


@router.message(AdminProduct.price)
async def add_price(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        price = float(msg.text.strip().replace(" ", "").replace(",", "."))
    except ValueError:
        await msg.answer("❌ Faqat raqam kiriting (masalan: 75000)", reply_markup=_CANCEL_KB)
        return

    await state.update_data(price=price)
    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.set_state(AdminProduct.image_url)
        await msg.answer(
            "🖼 Rasm URL kiriting yoki <b>—</b> yuboring (bo'sh qoldirish uchun):",
            parse_mode="HTML",
            reply_markup=_CANCEL_KB,
        )
    else:
        await _finish_field_edit(msg, state, "price", price)


@router.message(AdminProduct.image_url, F.photo | F.text)
async def add_image(msg: Message, bot: Bot, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return

    if msg.photo:
        await msg.answer("⏳ Rasm yuklanmoqda...")
        image_val = await _upload_to_imgbb(bot, msg.photo[-1].file_id)
        if not image_val:
            await msg.answer("❌ Rasm yuklashda xatolik. Qaytadan yuboring.", reply_markup=_CANCEL_KB)
            return
    else:
        image_val = None  # "—" yuborilsa bo'sh qoladi

    data = await state.get_data()
    if data.get("_mode") == "add":
        await state.clear()
        try:
            new_id = await create_product({
                "name_uz":   data["name_uz"],
                "name_ru":   data["name_ru"],
                "desc_uz":   data["desc_uz"],
                "desc_ru":   data["desc_ru"],
                "price":     data["price"],
                "image_url": image_val,
                "is_active": 1,
            })
        except Exception as e:
            log.error("create_product failed: %s", e)
            await msg.answer("❌ Xatolik yuz berdi.")
            return

        await push_products_json()
        await msg.answer(
            f"✅ Mahsulot <b>#{new_id}</b> qo'shildi!\n🖼 {image_val or '—'}",
            parse_mode="HTML",
            reply_markup=await _products_manage_kb(),
        )
    else:
        await _finish_field_edit(msg, state, "image_url", image_val)


# ── 8. FIELD TAHRIRLASH FSM ───────────────────────────────────────

@router.callback_query(ProdEditCB.filter())
async def cb_prod_edit_field(
    cb: CallbackQuery, callback_data: ProdEditCB, state: FSMContext
) -> None:
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return

    field_labels = {
        "name_uz":   "Nomi (UZ)",
        "name_ru":   "Nomi (RU)",
        "desc_uz":   "Tavsif (UZ)",
        "desc_ru":   "Tavsif (RU)",
        "price":     "Narx (so'mda)",
        "image_url": "Rasm URL",
    }
    state_map = {
        "name_uz":   AdminProduct.name_uz,
        "name_ru":   AdminProduct.name_ru,
        "desc_uz":   AdminProduct.desc_uz,
        "desc_ru":   AdminProduct.desc_ru,
        "price":     AdminProduct.price,
        "image_url": AdminProduct.image_url,
    }

    label = field_labels.get(callback_data.field, callback_data.field)
    await state.set_state(state_map[callback_data.field])
    await state.update_data(_mode="edit", _pid=callback_data.pid, _field=callback_data.field)
    await cb.message.answer(
        f"✏️ Yangi qiymatni kiriting — <b>{label}</b>:",
        parse_mode="HTML",
        reply_markup=_CANCEL_KB,
    )
    await cb.answer()


async def _finish_field_edit(msg: Message, state: FSMContext, field: str, value) -> None:
    """Bitta field yangilanishini tugating va detail sahifaga qayting."""
    data = await state.get_data()
    pid = data.get("_pid")
    await state.clear()

    if not pid:
        await msg.answer("❌ Xatolik: mahsulot ID topilmadi.")
        return

    try:
        await update_product_field(pid, field, value)
    except Exception as e:
        log.error("update_product_field failed: %s", e)
        await msg.answer("❌ Xatolik yuz berdi.")
        return

    await push_products_json()

    product = await get_product_by_id(pid)
    if not product:
        await msg.answer("✅ Yangilandi.")
        return

    text = (
        f"✅ Yangilandi!\n\n"
        f"📦 <b>{product['name_uz']} / {product['name_ru']}</b>\n\n"
        f"📄 <i>{product['desc_uz']}</i>\n"
        f"💰 Narx: <b>{product['price']:,.0f} so'm</b>\n"
        f"🖼 Rasm: {product['image_url'] or '—'}\n"
        f"{'✅ Faol' if product['is_active'] else '❌ Nofaol'}"
    )
    await msg.answer(
        text,
        parse_mode="HTML",
        reply_markup=_product_detail_kb(product["id"], bool(product["is_active"])),
    )


# ── Boot ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    await init_db()
    await push_products_json()
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    log.info("Bot starting…")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())