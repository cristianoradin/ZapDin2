"""
ZapDin — Sincronização de usuários: Monitor → App
==================================================
O monitor envia o token do cliente no header x-monitor-token.
O app localiza a empresa pelo token e escopa todas as operações a ela.
"""
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitor-sync", tags=["monitor-sync"])


async def _get_empresa_id(
    x_monitor_token: str = Header(..., alias="x-monitor-token"),
    db=Depends(get_db),
) -> int:
    """Localiza a empresa pelo token do cliente enviado pelo monitor."""
    if not x_monitor_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token não informado.")

    async with db.execute(
        "SELECT id FROM empresas WHERE token = ? AND ativo = TRUE", (x_monitor_token,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        logger.warning("[monitor-sync] Token inválido: %s...", x_monitor_token[:8])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")

    return row["id"]


class UserSyncPayload(BaseModel):
    username: str
    password: str
    menus: list | None = None  # None = todos os menus; lista = só esses menus


class SenhaPayload(BaseModel):
    password: str


class UsernamePayload(BaseModel):
    username: str


@router.get("/usuarios")
async def list_usuarios(
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    async with db.execute(
        "SELECT id, username, created_at FROM usuarios WHERE empresa_id = ? ORDER BY username",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {"usuarios": [dict(r) for r in rows]}


@router.post("/usuarios/sync")
async def sync_usuario(
    body: UserSyncPayload,
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    username = body.username.strip().lower()
    menus_json = json.dumps(body.menus) if body.menus is not None else None
    await db.execute(
        """INSERT INTO usuarios (empresa_id, username, password_hash, menus)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (empresa_id, username) DO UPDATE
           SET password_hash = EXCLUDED.password_hash,
               menus = EXCLUDED.menus""",
        (empresa_id, username, hash_password(body.password), menus_json),
    )
    await db.commit()
    logger.info("[monitor-sync] Usuário '%s' sincronizado na empresa %s (menus=%s).", username, empresa_id, body.menus)
    return {"ok": True}


@router.delete("/usuarios/{username}")
async def delete_usuario(
    username: str,
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    await db.execute(
        "DELETE FROM usuarios WHERE username = ? AND empresa_id = ?",
        (username.lower(), empresa_id),
    )
    await db.commit()
    logger.info("[monitor-sync] Usuário '%s' removido da empresa %s.", username, empresa_id)
    return {"ok": True}


@router.put("/usuarios/{username}/senha")
async def change_senha(
    username: str,
    body: SenhaPayload,
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    await db.execute(
        "UPDATE usuarios SET password_hash = ? WHERE username = ? AND empresa_id = ?",
        (hash_password(body.password), username.lower(), empresa_id),
    )
    await db.commit()
    return {"ok": True}


class MenusPayload(BaseModel):
    menus: list | None = None  # None = todos os menus; lista = só esses menus


@router.put("/usuarios/{username}/menus")
async def update_menus(
    username: str,
    body: MenusPayload,
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    menus_json = json.dumps(body.menus) if body.menus is not None else None
    await db.execute(
        "UPDATE usuarios SET menus = ? WHERE username = ? AND empresa_id = ?",
        (menus_json, username.lower(), empresa_id),
    )
    await db.commit()
    logger.info("[monitor-sync] Menus de '%s' atualizados na empresa %s: %s", username, empresa_id, body.menus)
    return {"ok": True}


@router.put("/usuarios/{username}/username")
async def rename_usuario(
    username: str,
    body: UsernamePayload,
    empresa_id: int = Depends(_get_empresa_id),
    db=Depends(get_db),
):
    try:
        await db.execute(
            "UPDATE usuarios SET username = ? WHERE username = ? AND empresa_id = ?",
            (body.username.strip().lower(), username.lower(), empresa_id),
        )
        await db.commit()
    except Exception:
        pass
    return {"ok": True}
