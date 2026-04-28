import os
from contextlib import asynccontextmanager
from datetime import datetime

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.database import init_db
from .routers import auth, clientes, monitor_router, versoes
from .routers.activation import router as activation_router
from .routers.grupos import router as grupos_router

_START_TIME = datetime.utcnow()

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
    yield


# ── App ────────────────────────────────────────────────────────────────────────
fastapi_app = FastAPI(title="ZapDin Monitor", version="2.0.0", lifespan=lifespan)

# ── CORS — permite o painel de monitoramento chamar a API ─────────────────────
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

fastapi_app.include_router(activation_router)
fastapi_app.include_router(auth.router)
fastapi_app.include_router(clientes.router)
fastapi_app.include_router(grupos_router)
fastapi_app.include_router(monitor_router.router)
fastapi_app.include_router(versoes.router)


# ── Endpoint público de status (sem autenticação) ─────────────────────────────
_DASHBOARD_KEY = os.environ.get("DASHBOARD_KEY", "zapdin-monitor-dash-2026")


@fastapi_app.get("/api/status")
async def public_status(key: str = "", db=None):
    """Endpoint público para o painel de monitoramento externo."""
    from .core.database import get_db as _get_db
    import aiosqlite
    from datetime import timedelta

    if key != _DASHBOARD_KEY:
        return JSONResponse({"error": "Chave inválida"}, status_code=401)

    uptime_s = int((datetime.utcnow() - _START_TIME).total_seconds())
    hours, rem = divmod(uptime_s, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    threshold = (datetime.utcnow() - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")

    db_path = os.path.join(os.path.dirname(__file__), "..", settings.database_url)
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT c.nome, c.cnpj, c.cidade, c.uf, c.versao_instalada,
                          (SELECT created_at FROM heartbeats WHERE cliente_id = c.id
                           ORDER BY created_at DESC LIMIT 1) as ultimo_ping,
                          (SELECT ip FROM heartbeats WHERE cliente_id = c.id
                           ORDER BY created_at DESC LIMIT 1) as ultimo_ip
                   FROM clientes c WHERE c.ativo = 1 ORDER BY c.nome"""
            ) as cur:
                rows = await cur.fetchall()

        clientes_list = []
        online = 0
        for r in rows:
            ativo = bool(r["ultimo_ping"] and r["ultimo_ping"] >= threshold)
            if ativo:
                online += 1
            clientes_list.append({
                "nome": r["nome"],
                "cnpj": r["cnpj"],
                "cidade": r["cidade"],
                "uf": r["uf"],
                "versao": r["versao_instalada"],
                "ultimo_ping": r["ultimo_ping"],
                "ip": r["ultimo_ip"],
                "online": ativo,
            })

        return {
            "servidor": "online",
            "uptime": uptime_str,
            "total_clientes": len(clientes_list),
            "online": online,
            "offline": len(clientes_list) - online,
            "clientes": clientes_list,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {
            "servidor": "online",
            "uptime": uptime_str,
            "total_clientes": 0,
            "online": 0,
            "offline": 0,
            "clientes": [],
            "erro": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


@fastapi_app.post("/api/logout")
async def logout_alias(request: Request):
    from .core.security import SESSION_COOKIE
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# Serve static SPA
_static_dir = os.path.join(os.path.dirname(__file__), "static")
fastapi_app.mount("/logo", StaticFiles(directory=os.path.join(_static_dir, "logo")), name="logo")


_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@fastapi_app.get("/login")
async def serve_login():
    return FileResponse(os.path.join(_static_dir, "login.html"), headers=_NO_CACHE)


@fastapi_app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    index = os.path.join(_static_dir, "index.html")
    if os.path.exists(index):
        return FileResponse(index, headers=_NO_CACHE)
    return JSONResponse({"error": "Frontend not found"}, status_code=404)


# ── ASGI wrapper with Socket.IO ────────────────────────────────────────────────
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


def main() -> None:
    uvicorn.run("monitor.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
