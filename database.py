"""
database.py — aiosqlite-backed data layer.

- Single DB file (path from config.DB_PATH).
- WAL mode for concurrent reads while bot writes.
- All public coroutines mirror the previous asyncpg API so bot.py is unchanged.
"""

from __future__ import annotations

import json

import aiosqlite

from config import DB_PATH


# ── Init ──────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await _create_schema(db)
        await _seed_products(db)
        await db.commit()


# ── Schema ────────────────────────────────────────────────────────────────────

async def _create_schema(db: aiosqlite.Connection) -> None:
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            full_name  TEXT,
            lang       TEXT    NOT NULL DEFAULT 'uz',
            phone      TEXT,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS products (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name_uz    TEXT    NOT NULL,
            name_ru    TEXT    NOT NULL,
            price      REAL    NOT NULL,
            image_url  TEXT,
            is_active  INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(user_id),
            items      TEXT    NOT NULL,
            total      REAL    NOT NULL,
            phone      TEXT,
            latitude   REAL,
            longitude  REAL,
            address    TEXT,
            duration   TEXT,
            order_type TEXT    NOT NULL DEFAULT 'product',
            status     TEXT    NOT NULL DEFAULT 'pending',
            screenshot TEXT,
            comment    TEXT,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)


async def _seed_products(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("SELECT COUNT(*) FROM products")
    row = await cursor.fetchone()
    if row and row[0]:
        return
    await db.executemany(
        "INSERT INTO products (name_uz, name_ru, price, image_url) VALUES (?,?,?,?)",
        [
            ("Mahsulot A", "Продукт A", 50000, "https://placehold.co/300x200/0ea5e9/fff?text=A"),
            ("Mahsulot B", "Продукт B", 75000, "https://placehold.co/300x200/0284c7/fff?text=B"),
            ("Mahsulot C", "Продукт C", 120000, "https://placehold.co/300x200/0369a1/fff?text=C"),
            ("Mahsulot D", "Продукт D", 95000, "https://placehold.co/300x200/075985/fff?text=D"),
        ],
    )


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_user(user_id: int, **kwargs: str | int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        if kwargs:
            assignments = ", ".join(f"{col} = ?" for col in kwargs)
            await db.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                (*kwargs.values(), user_id),
            )
        await db.commit()


# ── Products ──────────────────────────────────────────────────────────────────

async def get_products(active_only: bool = True) -> list[dict]:
    query = (
        "SELECT * FROM products WHERE is_active = 1 ORDER BY id"
        if active_only
        else "SELECT * FROM products ORDER BY id"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def toggle_product(product_id: int) -> bool:
    """Flip is_active. Returns new state. Raises ValueError if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT is_active FROM products WHERE id = ?", (product_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Product {product_id} not found")
        new_state = 0 if row[0] else 1
        await db.execute(
            "UPDATE products SET is_active = ? WHERE id = ?", (new_state, product_id)
        )
        await db.commit()
        return bool(new_state)


# ── Orders ────────────────────────────────────────────────────────────────────

async def create_order(data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders
                (user_id, items, total, phone, latitude, longitude,
                 address, duration, order_type, screenshot, comment)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["user_id"],
                data["items"],
                data["total"],
                data.get("phone"),
                data.get("latitude"),
                data.get("longitude"),
                data.get("address"),
                data.get("duration"),
                data.get("order_type", "product"),
                data.get("screenshot"),
                data.get("comment"),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def monthly_report() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(total), 0) AS revenue
            FROM orders
            WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
              AND status != 'cancelled'
            """
        )
        row = await cursor.fetchone()
        return {"cnt": row[0], "revenue": float(row[1])}