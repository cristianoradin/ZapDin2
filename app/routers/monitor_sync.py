"""
ZapDin — Sincronização de usuários: Monitor → App
==================================================
Endpoints acessíveis pela rede (não restritos a localhost),
autenticados via header X-Monitor-Token que deve bater com
settings.monitor_client_token do app.

Rotas:
  POST   /api/monitor-sync/usuarios/sync            → upsert de usuário
  DELETE /api/monitor-sync/usuarios/{username}      → remove usuário
  PUT    /api/monitor-sync/usuarios/{username}/senha    → troca senha
  PUT    /api/monitor-sync/usuarios/{username}/username → renomeia usuário
"""
import logging


from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..core.config import settings
from ..core.database import get_db
from ..core.security import hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitor-sync", tags=["monitor-sync"])


# ─────────────────────────────────────────────────────────────────────────────
#  Autenticação por token
# ─────────────────────────────────────────────────────────────────────────────

def _check_token(x_monitor_token: str = Header(..., alias="x-monitor-token")) -> None:
    """Valida que o token enviado bate com monitor_client_token do app."""
    expected = settings.monitor_client_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App não possui monitor_client_token configurado.",
        )
    if x_monitor_token != expected:
        logger.warning("[monitor-sync] Token inválido recebido.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Modelos
# ─────────────────────────────────────────────────────────────────────────────

class UserSyncPayload(BaseModel):
    username: str
    password: str  # senha em texto plano — hasheada aqui


class SenhaPayload(BaseModel):
    password: str


class UsernamePayload(BaseModel):
    username: str


# ─────────────────────────────────────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/usuarios")
async def list_usuarios(
    db=Depends(get_db),
    _: None = Depends(_check_token),
):
    """Lista usuários do app — chamado pelo Monitor para exibir usuários do posto."""
    async with db.execute(
        "SELECT id, username, created_at FROM usuarios ORDER BY username"
    ) as cur:
        rows = await cur.fetchall()
    return {"usuarios": [dict(r) for r in rows]}


@router.post("/usuarios/sync")
async def sync_usuario(
    body: UserSyncPayload,
    db=Depends(get_db),
    _: None = Depends(_check_token),
):
    """Cria ou atualiza (upsert) um usuário no banco do app."""
    username = body.username.strip().lower()
    await db.execute(
        """INSERT INTO usuarios (username, password_hash)
           VALUES (?, ?)
           ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash""",
        (username, hash_password(body.password)),
    )
    await db.commit()
    logger.info("[monitor-sync] Usuário '%s' sincronizado.", username)
    return {"ok": True}


@router.delete("/usuarios/{username}")
async def delete_usuario(
    username: str,
    db=Depends(get_db),
    _: None = Depends(_check_token),
):
    """Remove um usuário do banco do app."""
    await db.execute("DELETE FROM usuarios WHERE username = ?", (username.lower(),))
    await db.commit()
    logger.info("[monitor-sync] Usuário '%s' removido.", username)
    return {"ok": True}


@router.put("/usuarios/{username}/senha")
async def change_senha(
    username: str,
    body: SenhaPayload,
    db=Depends(get_db),
    _: None = Depends(_check_token),
):
    """Troca a senha de um usuário no banco do app."""
    await db.execute(
        "UPDATE usuarios SET password_hash=? WHERE username=?",
        (hash_password(body.password), username.lower()),
    )
    await db.commit()
    return {"ok": True}


@router.put("/usuarios/{username}/username")
async def rename_usuario(
    username: str,
    body: UsernamePayload,
    db=Depends(get_db),
    _: None = Depends(_check_token),
):
    """Renomeia um usuário no banco do app."""
    try:
        await db.execute(
            "UPDATE usuarios SET username=? WHERE username=?",
            (body.username.strip().lower(), username.lower()),
        )
        await db.commit()
    except Exception:
        pass  # conflict de username — ignora silenciosamente
    return {"ok": True}
