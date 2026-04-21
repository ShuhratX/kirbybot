"""
api.py — Nginx orqali WebApp ga xizmat qiluvchi aiohttp server.
GET /api/products — faqat faol mahsulotlarni qaytaradi.
Static fayllar Nginx tomonidan to'g'ridan-to'g'ri serve qilinadi.
"""
from __future__ import annotations
import json
from aiohttp import web
from database import get_products

async def handle_products(request: web.Request) -> web.Response:
    products = await get_products(active_only=True)
    return web.Response(
        text=json.dumps(products, ensure_ascii=False),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )

def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/products", handle_products)
    return app

if __name__ == "__main__":
    web.run_app(make_app(), host="127.0.0.1", port=8000)