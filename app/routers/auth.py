import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from ..core.config import settings
from ..core.database import get_db
from ..core.security import verify_password, hash_password, create_session_token, SESSION_COOKIE, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


async def _verificar_no_monitor(username: str, password: str) -> bool:
    """
    Valida credenciais no monitor quando o usuário não existe localmente.
    Retorna True se o monitor confirmar, False em qualquer outro caso.
    """
    try:
        if not settings.monitor_url or not settings.monitor_client_token:
            return False
        url = f"{settings.monitor_url.rstrip('/')}/api/auth/verificar"
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(url, json={
                "username": username,
                "password": password,
                "client_token": settings.monitor_client_token,
            })
        return r.status_code == 200
    except Exception as e:
        logger.warning("Verificação no monitor falhou: %s", e)
        return False


@router.post("/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    username = body.username.strip().lower()

    async with db.execute(
        "SELECT id, username, password_hash FROM usuarios WHERE username = ?", (username,)
    ) as cur:
        row = await cur.fetchone()

    if row and verify_password(body.password, row["password_hash"]):
        # Usuário existe localmente e senha confere
        uid = row["id"]
    else:
        # Não encontrado localmente (ou senha diferente) — tenta o monitor
        if not await _verificar_no_monitor(username, body.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

        # Monitor validou: faz upsert no banco local para sessões futuras
        try:
            cur2 = await db.execute(
                """INSERT INTO usuarios (username, password_hash)
                   VALUES (?, ?)
                   ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash""",
                (username, hash_password(body.password)),
            )
            uid = cur2.lastrowid or (
                (await (await db.execute(
                    "SELECT id FROM usuarios WHERE username=?", (username,)
                )).fetchone())["id"]
            )
            await db.commit()
        except Exception as e:
            logger.warning("Upsert local após validação do monitor falhou: %s", e)
            # Mesmo assim deixa logar — usa uid=0 como fallback
            uid = 0

    token = create_session_token(uid, username)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return {"ok": True, "username": username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    import httpx
    from ..core.config import settings

    menus = None  # None = todos os menus liberados (fallback seguro)
    try:
        if settings.monitor_url and settings.monitor_client_token:
            url = (
                f"{settings.monitor_url.rstrip('/')}"
                f"/api/auth/usuario-menus/{user['usr']}"
                f"?client_token={settings.monitor_client_token}"
            )
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if not data.get("all_allowed", True):
                    menus = data.get("menus")
    except Exception:
        pass  # monitor offline ou erro → libera todos os menus

    return {
        "username": user["usr"],
        "uid": user["uid"],
        "client_name": settings.client_name,
        "menus": menus,  # None = todos; lista = apenas esses menus
    }
