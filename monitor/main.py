import os
from contextlib import asynccontextmanager

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.database import init_db
from .routers import auth, clientes, monitor_router, versoes

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
fastapi_app = FastAPI(title="ZapDin Monitor", version="1.0.0", lifespan=lifespan)

fastapi_app.include_router(auth.router)
fastapi_app.include_router(clientes.router)
fastapi_app.include_router(monitor_router.router)
fastapi_app.include_router(versoes.router)


@fastapi_app.post("/api/logout")
async def logout_alias(request: Request):
    from .core.security import SESSION_COOKIE
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


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
    uvicorn.run("monitor.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
