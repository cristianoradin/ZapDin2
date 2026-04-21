import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite
import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.database import init_db, get_db
from .routers import auth, whatsapp, erp, config_router, arquivos, stats
from .services import reporter, updater
from .services.whatsapp_service import wa_manager

# ── Socket.IO ──────────────────────────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


@sio.event
async def connect(sid, environ):
    pass


@sio.event
async def disconnect(sid):
    pass


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Load existing WA sessions from DB
    async with aiosqlite.connect(settings.database_url) as db:
        db.row_factory = aiosqlite.Row
        await wa_manager.load_from_db(db)

    reporter.start()
    updater.start()

    yield

    reporter.stop()
    updater.stop()


# ── App ────────────────────────────────────────────────────────────────────────
fastapi_app = FastAPI(title="ZapDin App", version="1.0.0", lifespan=lifespan)

fastapi_app.include_router(auth.router)
fastapi_app.include_router(whatsapp.router)
fastapi_app.include_router(erp.router)
fastapi_app.include_router(config_router.router)
fastapi_app.include_router(arquivos.router)
fastapi_app.include_router(stats.router)


@fastapi_app.post("/api/logout")
async def logout_alias(request: Request):
    from fastapi.responses import JSONResponse
    from .core.security import SESSION_COOKIE
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@fastapi_app.get("/api/qr/{sessao_id}")
async def qr_alias(sessao_id: str):
    qr = wa_manager.get_qr(sessao_id)
    if qr is None:
        return JSONResponse({"error": "QR não disponível"}, status_code=404)
    return {"qr": qr}


@fastapi_app.post("/api/report")
async def report_endpoint(request: Request):
    """Recebe heartbeat de sub-sistemas internos (para compatibilidade)."""
    return {"ok": True}


# Serve static SPA
_static_dir = os.path.join(os.path.dirname(__file__), "static")
fastapi_app.mount("/logo", StaticFiles(directory=os.path.join(_static_dir, "logo")), name="logo")


@fastapi_app.get("/login")
async def serve_login():
    return FileResponse(os.path.join(_static_dir, "login.html"))


@fastapi_app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    index = os.path.join(_static_dir, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


# ── ASGI wrapper with Socket.IO ────────────────────────────────────────────────
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


def main() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
