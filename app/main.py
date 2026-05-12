import asyncio
import os
from contextlib import asynccontextmanager

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .core.config import settings
from .core.database import init_db, get_db, get_db_direct
from .routers import auth, whatsapp, erp, config_router, arquivos, stats, telegram_router
from .routers.activation import router as activation_router
from .routers.internal import router as internal_router
from .routers.monitor_sync import router as monitor_sync_router
from .routers.docs_router import router as docs_router
from .routers.campanha import router as campanha_router
from .routers.pdv_router import router as pdv_router
from .services import reporter, updater, telegram_service, queue_worker
from .services.whatsapp_service import wa_manager as _playwright_manager
from .services.evolution_service import evo_manager as _evo_manager

# Seleciona o backend de WhatsApp conforme configuração
wa_manager = _evo_manager if settings.use_evolution else _playwright_manager

# ── Socket.IO ──────────────────────────────────────────────────────────────────
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


@sio.event
async def connect(sid, environ):
    pass


@sio.event
async def disconnect(sid):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  Middleware de Lock
#  Quando APP_STATE=locked, bloqueia tudo exceto as rotas de ativação.
# ─────────────────────────────────────────────────────────────────────────────

_LOCK_ALLOWED_PREFIXES = (
    "/activate",
    "/api/activate",
    "/api/evo-webhook",   # Evolution API envia eventos mesmo durante ativação
    "/api/evo-file/",
    "/login",
    "/static/",
    "/logo/",
    "/favicon",
)


class LockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if settings.is_locked:
            path = request.url.path
            allowed = any(path.startswith(p) for p in _LOCK_ALLOWED_PREFIXES)
            if not allowed:
                if path.startswith("/api/") or path.startswith("/internal/"):
                    return JSONResponse(
                        {"error": "Sistema bloqueado. Conclua a ativação em /activate."},
                        status_code=403,
                    )
                return RedirectResponse(url="/activate", status_code=302)
        return await call_next(request)


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    if not settings.is_locked:
        # Carrega sessões WA de todas as empresas
        async with get_db_direct() as db:
            await wa_manager.load_from_db(db)
            # Telegram: carrega config do primeiro tenant que tiver configurado
            async with db.execute(
                "SELECT value FROM config WHERE key='tg_bot_token' LIMIT 1"
            ) as cur:
                tg_token_row = await cur.fetchone()
            async with db.execute(
                "SELECT value FROM config WHERE key='tg_chat_id' LIMIT 1"
            ) as cur:
                tg_chat_row = await cur.fetchone()
            if tg_token_row and tg_chat_row:
                telegram_service.configure(tg_token_row["value"], tg_chat_row["value"])

        reporter.start()
        updater.start()
        telegram_service.start()
        queue_worker.start()  # processa mensagens, arquivos e campanha_envios

    yield

    # ── Cleanup: para todas as sessões Playwright antes de encerrar ──────────
    # Sem isso, os processos Node.js do Playwright ficam órfãos e causam EPIPE
    # na próxima inicialização do app.
    import asyncio as _asyncio
    stop_tasks = [
        _asyncio.create_task(sess.stop())
        for sess in list(wa_manager._sessions.values())
    ]
    if stop_tasks:
        await _asyncio.gather(*stop_tasks, return_exceptions=True)

    reporter.stop()
    updater.stop()
    telegram_service.stop()
    queue_worker.stop()


# ── App ────────────────────────────────────────────────────────────────────────
fastapi_app = FastAPI(title="ZapDin App", version="2.0.0", lifespan=lifespan)

# Middleware (adicionado antes dos routers)
fastapi_app.add_middleware(LockMiddleware)

# Routers
fastapi_app.include_router(activation_router)   # /activate + /api/activate
fastapi_app.include_router(internal_router)     # /internal/* (localhost only)
fastapi_app.include_router(monitor_sync_router) # /api/monitor-sync/* (token auth, rede)
fastapi_app.include_router(auth.router)
fastapi_app.include_router(whatsapp.router)
fastapi_app.include_router(erp.router)
fastapi_app.include_router(config_router.router)
fastapi_app.include_router(arquivos.router)
fastapi_app.include_router(stats.router)
fastapi_app.include_router(telegram_router.router)
fastapi_app.include_router(docs_router)             # /api/docs/* (documentação)
fastapi_app.include_router(campanha_router)         # /api/campanha/* (disparo em massa)
fastapi_app.include_router(pdv_router)              # /api/pdv/* (ZapDin PDV local)


@fastapi_app.post("/api/logout")
async def logout_alias(request: Request):
    from .core.security import SESSION_COOKIE
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@fastapi_app.post("/api/report")
async def report_endpoint(request: Request):
    return {"ok": True}


@fastapi_app.get("/api/evo-file/{token}")
async def evo_file_serve(token: str):
    """Serve arquivos temporários para a Evolution API buscar via URL local."""
    from .services.evolution_service import _file_tokens, _file_tokens_lock
    with _file_tokens_lock:
        path = _file_tokens.get(token)
    if not path or not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@fastapi_app.post("/api/evo-webhook")
async def evo_webhook(request: Request):
    """Recebe eventos da Evolution API em tempo real (QR, conexão, etc.)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    wa_manager.handle_webhook(payload)
    return {"ok": True}


# ── Arquivos estáticos ────────────────────────────────────────────────────────
_static_dir = os.path.join(os.path.dirname(__file__), "static")
_logo_dir = os.path.join(_static_dir, "logo")
if os.path.isdir(_logo_dir):
    fastapi_app.mount("/logo", StaticFiles(directory=_logo_dir), name="logo")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@fastapi_app.get("/login")
async def serve_login():
    return FileResponse(os.path.join(_static_dir, "login.html"), headers=_NO_CACHE)


@fastapi_app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("internal/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    index = os.path.join(_static_dir, "index.html")
    if os.path.exists(index):
        return FileResponse(index, headers=_NO_CACHE)
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


# ── ASGI wrapper com Socket.IO ─────────────────────────────────────────────────
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


def main() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
