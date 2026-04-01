TEXTS = {
    "uz": {
        "choose_lang":    "🌐 Tilni tanlang / Выберите язык:",
        "enter_name":     "👤 Ism va familiyangizni kiriting:",
        "welcome":        "Xush kelibsiz, {name}! 🎉",
        "main_menu":      "Asosiy menyu:",
        "order_btn":      "🛒 Buyurtma berish",
        "change_lang":    "🌐 Tilni o'zgartirish",
        "order_received": "✅ Buyurtmangiz qabul qilindi!",
    },
    "ru": {
        "choose_lang":    "🌐 Tilni tanlang / Выберите язык:",
        "enter_name":     "👤 Введите ваше имя и фамилию:",
        "welcome":        "Добро пожаловать, {name}! 🎉",
        "main_menu":      "Главное меню:",
        "order_btn":      "🛒 Сделать заказ",
        "change_lang":    "🌐 Сменить язык",
        "order_received": "✅ Ваш заказ принят!",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text