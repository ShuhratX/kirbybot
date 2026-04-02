import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from database import get_products, init_db, monthly_report, toggle_product


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Order Bot WebApp", lifespan=lifespan)

BASE = os.path.dirname(__file__)

# Serve the webapp directory as static files (index.html + assets)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "webapp/static")), name="static")


# ── WebApp ────────────────────────────────────────────────────────────────────

@app.get("/webapp")
async def webapp():
    """Serve the static webapp HTML. lang/user_id are consumed by JS via URLSearchParams."""
    return FileResponse(os.path.join(BASE, "webapp/index.html"))


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/products")
async def api_products():
    return await get_products(active_only=True)


@app.post("/api/toggle/{product_id}")
async def api_toggle(product_id: int):
    try:
        new_state = await toggle_product(product_id)
        return {"active": new_state}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/report")
async def api_report():
    return await monthly_report()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp_server:app", host="0.0.0.0", port=8000, reload=True)