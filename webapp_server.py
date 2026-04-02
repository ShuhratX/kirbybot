import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from config import PAYMENT_CARD, PAYMENT_OWNER
from database import get_products, init_db, monthly_report, toggle_product


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()   # creates pool + schema
    yield


app = FastAPI(title="Order Bot WebApp", lifespan=lifespan)

BASE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "webapp/static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "webapp/templates"))


# ── User WebApp ───────────────────────────────────────────────────────────────

@app.get("/webapp", response_class=HTMLResponse)
async def webapp(request: Request, lang: str = "uz", user_id: int = 0):
    products = await get_products(active_only=True)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "lang": lang,
        "user_id": user_id,
        "products": products,
        "payment_card": PAYMENT_CARD,
        "payment_owner": PAYMENT_OWNER,
    })


# ── Admin WebApp ──────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, user_id: int = 0):
    products = await get_products(active_only=False)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user_id": user_id,
        "products": products,
    })


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/products")
async def api_products():
    return await get_products(active_only=True)


@app.post("/api/toggle/{product_id}")
async def api_toggle(product_id: int):
    try:
        new_state = await toggle_product(product_id)
        return {"active": new_state}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/report")
async def api_report():
    return await monthly_report()


@app.post("/api/upload-screenshot")
async def upload_screenshot(file: UploadFile = File(...)):
    upload_dir = os.path.join(BASE, "webapp/static/uploads")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{os.urandom(8).hex()}_{file.filename}"
    filepath = os.path.join(upload_dir, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    return {"url": f"/static/uploads/{filename}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp_server:app", host="0.0.0.0", port=8000, reload=True)