# ═══════════════════════════════════════════════════════════════════
#  PATCH: bot.py ga qo'shiladigan admin mahsulot CRUD
#  Joylashuv: bot.py ichida mavjud kodga qo'shiladi
# ═══════════════════════════════════════════════════════════════════

# ── 1. IMPORTS (mavjud import blokiga qo'shing) ───────────────────
from aiogram.filters.callback import CallbackData

# ── 2. FSM STATES (mavjud StatesGroup lardan keyin) ───────────────

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


@router.message(AdminProduct.image_url)
async def add_image_url(msg: Message, state: FSMContext) -> None:
    if msg.from_user.id not in ADMIN_IDS:
        return
    raw = msg.text.strip()
    image_url = None if raw in ("—", "-", "") else raw

    data = await state.get_data()
    if data.get("_mode") == "add":
        # Barcha ma'lumotlar to'plandi — DB ga yoz
        await state.clear()
        try:
            new_id = await create_product({
                "name_uz":   data["name_uz"],
                "name_ru":   data["name_ru"],
                "desc_uz":   data["desc_uz"],
                "desc_ru":   data["desc_ru"],
                "price":     data["price"],
                "image_url": image_url,
                "is_active": 1,
            })
        except Exception as e:
            log.error("create_product failed: %s", e)
            await msg.answer("❌ Xatolik yuz berdi. Qaytadan urinib ko'ring.")
            return

        await push_products_json()
        await msg.answer(
            f"✅ Mahsulot <b>#{new_id}</b> muvaffaqiyatli qo'shildi!",
            parse_mode="HTML",
            reply_markup=await _products_manage_kb(),
        )
    else:
        await _finish_field_edit(msg, state, "image_url", image_url)


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