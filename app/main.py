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
from .services import reporter, updater, telegram_service
from .services.whatsapp_service import wa_manager

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
        # Carrega sessões WA e config Telegram apenas quando o sistema está ativo
        async with get_db_direct() as db:
            await wa_manager.load_from_db(db)
            async with db.execute(
                "SELECT key, value FROM config WHERE key IN ('tg_bot_token','tg_chat_id')"
            ) as cur:
                rows = await cur.fetchall()
            cfg = {r["key"]: r["value"] for r in rows}
            if cfg.get("tg_bot_token") and cfg.get("tg_chat_id"):
                telegram_service.configure(cfg["tg_bot_token"], cfg["tg_chat_id"])

        reporter.start()
        updater.start()
        telegram_service.start()
        # NOTA: queue_worker NÃO é iniciado aqui.
        # Em produção, roda como serviço separado: ZapDinWorker (NSSM).
        # Para desenvolver com tudo em um único processo, use:
        #   from .services import queue_worker; queue_worker.start()

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


@fastapi_app.post("/api/logout")
async def logout_alias(request: Request):
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
