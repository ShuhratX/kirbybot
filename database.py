import aiosqlite
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                full_name   TEXT,
                lang        TEXT DEFAULT 'uz',
                phone       TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name_uz     TEXT NOT NULL,
                name_ru     TEXT NOT NULL,
                price       REAL NOT NULL,
                image_url   TEXT,
                is_active   INTEGER DEFAULT 1,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                items       TEXT NOT NULL,
                total       REAL NOT NULL,
                phone       TEXT,
                latitude    REAL,
                longitude   REAL,
                address     TEXT,
                duration    TEXT,
                order_type  TEXT DEFAULT 'product',
                status      TEXT DEFAULT 'pending',
                screenshot  TEXT,
                comment     TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        cursor = await db.execute("SELECT COUNT(*) FROM products")
        row = await cursor.fetchone()
        if row[0] == 0:
            await db.executemany(
                "INSERT INTO products (name_uz, name_ru, price, image_url) VALUES (?,?,?,?)",
                [
                    ("Mahsulot A", "Продукт A", 50000, "https://via.placeholder.com/300x200/0ea5e9/fff?text=A"),
                    ("Mahsulot B", "Продукт B", 75000, "https://via.placeholder.com/300x200/0284c7/fff?text=B"),
                    ("Mahsulot C", "Продукт C", 120000, "https://via.placeholder.com/300x200/0369a1/fff?text=C"),
                    ("Mahsulot D", "Продукт D", 95000, "https://via.placeholder.com/300x200/075985/fff?text=D"),
                ],
            )
        await db.commit()


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_user(user_id: int, **kwargs) -> None:
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
            (user_id,),
        )
        if fields:
            await db.execute(f"UPDATE users SET {fields} WHERE user_id = ?", values)
        await db.commit()


# ── Products ──────────────────────────────────────────────────────────────────

async def get_products(active_only: bool = True) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM products" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY id"
        cursor = await db.execute(q)
        return [dict(r) for r in await cursor.fetchall()]


async def toggle_product(product_id: int) -> bool:
    """Toggle active state. Returns new state as bool. Raises ValueError if product not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_active FROM products WHERE id = ?", (product_id,))
        row = await cursor.fetchone()
        # FIX #9: Guard against None (invalid product_id)
        if row is None:
            raise ValueError(f"Product {product_id} not found")
        new_state = 0 if row[0] else 1
        await db.execute("UPDATE products SET is_active = ? WHERE id = ?", (new_state, product_id))
        await db.commit()
        return bool(new_state)


# ── Orders ────────────────────────────────────────────────────────────────────

async def create_order(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO orders
               (user_id, items, total, phone, latitude, longitude, address,
                duration, order_type, screenshot, comment)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["user_id"], data["items"], data["total"],
                data.get("phone"), data.get("latitude"), data.get("longitude"),
                data.get("address"), data.get("duration"), data.get("order_type", "product"),
                data.get("screenshot"), data.get("comment"),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def monthly_report() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as revenue
               FROM orders
               WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
                 AND status != 'cancelled'"""
        )
        row = await cursor.fetchone()
        return dict(row)
