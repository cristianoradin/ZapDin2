"""
ZapDin — Sincronização de usuários: Monitor → App  [DEPRECIADO]
==================================================
No modelo multi-tenant, cada empresa gerencia seus próprios usuários
diretamente via /api/auth/usuarios (autenticados com a sessão da empresa).

Este router é mantido apenas para compatibilidade de chamadas do Monitor,
mas retorna 410 Gone para sinalizar que não é mais utilizado.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..core.config import settings
from ..core.database import get_db
from ..core.security import hash_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitor-sync", tags=["monitor-sync"])


def _check_token(x_monitor_token: str = Header(..., alias="x-monitor-token")) -> None:
    expected = settings.monitor_client_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App não possui monitor_client_token configurado.",
        )
    if x_monitor_token != expected:
        logger.warning("[monitor-sync] Token inválido recebido.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.")


class UserSyncPayload(BaseModel):
    username: str
    password: str


class SenhaPayload(BaseModel):
    password: str


class UsernamePayload(BaseModel):
    username: str


_DEPRECATED = HTTPException(
    status_code=status.HTTP_410_GONE,
    detail=(
        "Este endpoint foi depreciado na versão multi-tenant. "
        "Usuários agora são gerenciados por empresa via /api/auth/usuarios."
    ),
)


@router.get("/usuarios")
async def list_usuarios(_: None = Depends(_check_token)):
    raise _DEPRECATED


@router.post("/usuarios/sync")
async def sync_usuario(body: UserSyncPayload, _: None = Depends(_check_token)):
    raise _DEPRECATED


@router.delete("/usuarios/{username}")
async def delete_usuario(username: str, _: None = Depends(_check_token)):
    raise _DEPRECATED


@router.put("/usuarios/{username}/senha")
async def change_senha(username: str, body: SenhaPayload, _: None = Depends(_check_token)):
    raise _DEPRECATED


@router.put("/usuarios/{username}/username")
async def rename_usuario(username: str, body: UsernamePayload, _: None = Depends(_check_token)):
    raise _DEPRECATED
