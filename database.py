"""
database.py — asyncpg-backed data layer for Order Bot.

Design decisions:
- Single connection pool (created once via init_db, reused everywhere).
- $1/$2/… placeholders — asyncpg does NOT support "?" style.
- SERIAL / BIGSERIAL for auto-increment columns (not AUTOINCREMENT).
- ON CONFLICT DO NOTHING / DO UPDATE for upsert (PostgreSQL syntax).
- All public coroutines are typed; callers rely on dict returns.
"""

from __future__ import annotations

import asyncpg

from config import DATABASE_URL

# Module-level pool — initialised in init_db(), shared across all calls.
_pool: asyncpg.Pool | None = None


# ── Pool helpers ──────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create connection pool and ensure schema + seed data exist."""
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await _create_schema(conn)
        await _seed_products(conn)


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first.")
    return _pool


# ── Schema ────────────────────────────────────────────────────────────────────

async def _create_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     BIGINT PRIMARY KEY,
            full_name   TEXT,
            lang        TEXT    NOT NULL DEFAULT 'uz',
            phone       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS products (
            id          SERIAL PRIMARY KEY,
            name_uz     TEXT    NOT NULL,
            name_ru     TEXT    NOT NULL,
            price       NUMERIC(14, 2) NOT NULL,
            image_url   TEXT,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS orders (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT  NOT NULL REFERENCES users(user_id),
            items       TEXT    NOT NULL,
            total       NUMERIC(14, 2) NOT NULL,
            phone       TEXT,
            latitude    DOUBLE PRECISION,
            longitude   DOUBLE PRECISION,
            address     TEXT,
            duration    TEXT,
            order_type  TEXT    NOT NULL DEFAULT 'product',
            status      TEXT    NOT NULL DEFAULT 'pending',
            screenshot  TEXT,
            comment     TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)


async def _seed_products(conn: asyncpg.Connection) -> None:
    count: int = await conn.fetchval("SELECT COUNT(*) FROM products")
    if count:
        return
    await conn.executemany(
        "INSERT INTO products (name_uz, name_ru, price, image_url) VALUES ($1, $2, $3, $4)",
        [
            ("Mahsulot A", "Продукт A", 50000, "https://via.placeholder.com/300x200/0ea5e9/fff?text=A"),
            ("Mahsulot B", "Продукт B", 75000, "https://via.placeholder.com/300x200/0284c7/fff?text=B"),
            ("Mahsulot C", "Продукт C", 120000, "https://via.placeholder.com/300x200/0369a1/fff?text=C"),
            ("Mahsulot D", "Продукт D", 95000, "https://via.placeholder.com/300x200/075985/fff?text=D"),
        ],
    )


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id
        )
        return dict(row) if row else None


async def upsert_user(user_id: int, **kwargs: str | int) -> None:
    """Insert user if absent, then apply any supplied field updates."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id,
        )
        if not kwargs:
            return
        # Build SET clause dynamically: lang=$2, full_name=$3, …
        assignments = ", ".join(
            f"{col} = ${i}" for i, col in enumerate(kwargs, start=2)
        )
        values = [user_id, *kwargs.values()]
        await conn.execute(
            f"UPDATE users SET {assignments} WHERE user_id = $1",
            *values,
        )


# ── Products ──────────────────────────────────────────────────────────────────

async def get_products(active_only: bool = True) -> list[dict]:
    query = (
        "SELECT * FROM products WHERE is_active = TRUE ORDER BY id"
        if active_only
        else "SELECT * FROM products ORDER BY id"
    )
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query)
        # Normalise is_active to int (1/0) so Jinja2 / JS behaviour is unchanged.
        return [{**dict(r), "is_active": int(r["is_active"])} for r in rows]


async def toggle_product(product_id: int) -> bool:
    """Flip is_active flag. Returns new state. Raises ValueError if product not found."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_active FROM products WHERE id = $1", product_id
        )
        if row is None:
            raise ValueError(f"Product {product_id} not found")
        new_state: bool = not row["is_active"]
        await conn.execute(
            "UPDATE products SET is_active = $1 WHERE id = $2",
            new_state, product_id,
        )
        return new_state


# ── Orders ────────────────────────────────────────────────────────────────────

async def create_order(data: dict) -> int:
    async with get_pool().acquire() as conn:
        order_id: int = await conn.fetchval(
            """
            INSERT INTO orders
                (user_id, items, total, phone, latitude, longitude,
                 address, duration, order_type, screenshot, comment)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id
            """,
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
        )
        return order_id


async def monthly_report() -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)                        AS cnt,
                COALESCE(SUM(total), 0)         AS revenue
            FROM orders
            WHERE DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
              AND status <> 'cancelled'
            """
        )
        return {"cnt": row["cnt"], "revenue": float(row["revenue"])}