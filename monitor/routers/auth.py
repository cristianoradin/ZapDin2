import json
import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
import asyncpg

from ..core.config import settings
from ..core.database import get_db
from ..core.security import verify_password, hash_password, create_session_token, SESSION_COOKIE, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Todos os menus disponíveis no app de envio
ALL_APP_MENUS = ["dashboard", "mensagem", "whatsapp", "teste", "token", "arquivo", "docs", "telegram"]


# ── Helpers de sincronização Monitor → App ────────────────────────────────────

def _sync_headers() -> dict:
    """Header de autenticação para os endpoints /api/monitor-sync/ do app."""
    return {"x-monitor-token": settings.app_sync_token}


async def _app_sync_create(username: str, password: str) -> None:
    """Cria/atualiza usuário no app de envio via /api/monitor-sync/."""
    if not settings.app_sync_token:
        logger.warning("Sync usuário → app ignorado: APP_SYNC_TOKEN não configurado no monitor.")
        return
    try:
        url = f"{settings.app_url.rstrip('/')}/api/monitor-sync/usuarios/sync"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"username": username, "password": password},
                              headers=_sync_headers())
    except Exception as e:
        logger.warning("Sync usuário → app falhou: %s", e)


async def _app_sync_delete(username: str) -> None:
    if not settings.app_sync_token:
        return
    try:
        url = f"{settings.app_url.rstrip('/')}/api/monitor-sync/usuarios/{username}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.delete(url, headers=_sync_headers())
    except Exception as e:
        logger.warning("Delete usuário → app falhou: %s", e)


async def _app_sync_senha(username: str, password: str) -> None:
    if not settings.app_sync_token:
        return
    try:
        url = f"{settings.app_url.rstrip('/')}/api/monitor-sync/usuarios/{username}/senha"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.put(url, json={"password": password}, headers=_sync_headers())
    except Exception as e:
        logger.warning("Troca senha → app falhou: %s", e)


async def _app_sync_username(old: str, new: str) -> None:
    if not settings.app_sync_token:
        return
    try:
        url = f"{settings.app_url.rstrip('/')}/api/monitor-sync/usuarios/{old}/username"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.put(url, json={"username": new}, headers=_sync_headers())
    except Exception as e:
        logger.warning("Rename usuário → app falhou: %s", e)


class UsuarioCreate(BaseModel):
    username: str
    password: str
    cliente_ids: List[int] = []
    menus: Optional[List[str]] = None  # None = todos os menus permitidos
    monitor_only: bool = False  # True = apenas monitor, não sincroniza com o app de envio


class ClienteAccess(BaseModel):
    cliente_ids: List[int]


class MenusUpdate(BaseModel):
    menus: Optional[List[str]] = None  # None = todos os menus


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response, db=Depends(get_db)):
    # Verifica primeiro na tabela admins, depois em usuarios
    row = None
    role = "admin"
    for table in ("admins", "usuarios"):
        async with db.execute(
            f"SELECT id, username, password_hash FROM {table} WHERE username = ?", (body.username,)
        ) as cur:
            row = await cur.fetchone()
        if row and verify_password(body.password, row["password_hash"]):
            role = "admin" if table == "admins" else "usuario"
            break
        row = None

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    token = create_session_token(row["id"], row["username"], role)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )

    result: dict = {"ok": True, "username": row["username"], "role": role}

    # Para usuarios: busca os clientes vinculados para exibir a tela de seleção
    if role == "usuario":
        async with db.execute(
            """SELECT c.id, c.nome
               FROM clientes c
               JOIN usuario_clientes uc ON uc.cliente_id = c.id
               WHERE uc.usuario_id = ?
               ORDER BY c.nome""",
            (row["id"],),
        ) as cur:
            result["clientes"] = [dict(r) for r in await cur.fetchall()]

    return result


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"username": user["usr"], "uid": user["uid"], "role": user.get("role", "admin")}


# ── Listagem de usuários com clientes vinculados ──────────────────────────────
@router.get("/usuarios")
async def list_usuarios(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        "SELECT id, username, menus, created_at FROM usuarios ORDER BY username"
    ) as cur:
        usuarios = [dict(r) for r in await cur.fetchall()]

    # Para cada usuário, busca os clientes vinculados
    for u in usuarios:
        async with db.execute(
            """SELECT c.id, c.nome
               FROM clientes c
               JOIN usuario_clientes uc ON uc.cliente_id = c.id
               WHERE uc.usuario_id = ?
               ORDER BY c.nome""",
            (u["id"],),
        ) as cur:
            u["clientes"] = [dict(r) for r in await cur.fetchall()]

    return usuarios


# ── Criar usuário (com clientes opcionais) ────────────────────────────────────
@router.post("/usuarios", status_code=status.HTTP_201_CREATED)
async def create_usuario(
    body: UsuarioCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    username = body.username.strip().lower()
    if not username or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Username inválido ou senha muito curta (mín. 6 chars).")
    menus_json = json.dumps(body.menus) if body.menus is not None else None
    try:
        cur = await db.execute(
            "INSERT INTO usuarios (username, password_hash, menus) VALUES (?, ?, ?)",
            (username, hash_password(body.password), menus_json),
        )
        usuario_id = cur.lastrowid

        # Vincula clientes
        for cid in body.cliente_ids:
            try:
                await db.execute(
                    "INSERT INTO usuario_clientes (usuario_id, cliente_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (usuario_id, cid),
                )
            except Exception:
                pass

        await db.commit()
        # Sincroniza com o app de envio apenas se NÃO for usuário exclusivo do monitor
        if not body.monitor_only:
            await _app_sync_create(username, body.password)
        return {"id": usuario_id, "username": username}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Username já existe.")


# ── Atualizar clientes de um usuário ─────────────────────────────────────────
@router.put("/usuarios/{usuario_id}/clientes")
async def set_usuario_clientes(
    usuario_id: int,
    body: ClienteAccess,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    # Remove todos os vínculos anteriores
    await db.execute(
        "DELETE FROM usuario_clientes WHERE usuario_id = ?", (usuario_id,)
    )
    # Insere os novos
    for cid in body.cliente_ids:
        try:
            await db.execute(
                "INSERT INTO usuario_clientes (usuario_id, cliente_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                (usuario_id, cid),
            )
        except Exception:
            pass
    await db.commit()
    return {"ok": True}


# ── Deletar usuário ───────────────────────────────────────────────────────────
@router.delete("/usuarios/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_usuario(
    usuario_id: int,
    db=Depends(get_db),
    current: dict = Depends(get_current_user),
):
    if current["uid"] == usuario_id:
        raise HTTPException(status_code=400, detail="Você não pode remover seu próprio usuário.")
    # Busca username antes de deletar para sincronizar com o app
    async with db.execute("SELECT username FROM usuarios WHERE id=?", (usuario_id,)) as cur:
        row = await cur.fetchone()
    await db.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    await db.commit()
    if row:
        await _app_sync_delete(row["username"])


# ── Trocar senha ──────────────────────────────────────────────────────────────
@router.put("/usuarios/{usuario_id}/senha")
async def change_senha(
    usuario_id: int,
    body: UsuarioCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Senha muito curta (mín. 6 chars).")
    async with db.execute("SELECT username FROM usuarios WHERE id=?", (usuario_id,)) as cur:
        row = await cur.fetchone()
    await db.execute(
        "UPDATE usuarios SET password_hash=? WHERE id=?",
        (hash_password(body.password), usuario_id),
    )
    await db.commit()
    if row:
        await _app_sync_senha(row["username"], body.password)
    return {"ok": True}


# ── Atualizar menus permitidos ────────────────────────────────────────────────
@router.put("/usuarios/{usuario_id}/menus")
async def set_usuario_menus(
    usuario_id: int,
    body: MenusUpdate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    menus_json = json.dumps(body.menus) if body.menus is not None else None
    await db.execute(
        "UPDATE usuarios SET menus=? WHERE id=?",
        (menus_json, usuario_id),
    )
    await db.commit()
    return {"ok": True}


# ── Verificação de credenciais (usada pelo app de envio no login) ─────────────
class VerificarRequest(BaseModel):
    username: str
    password: str
    client_token: str  # token do posto para autenticar a requisição


@router.post("/verificar")
async def verificar_credenciais(
    body: VerificarRequest,
    db=Depends(get_db),
):
    """
    Valida username/password de um usuário do monitor.
    Chamado pelo app de envio quando o usuário não existe no banco local.
    Autenticado pelo token do posto (client_token).
    """
    # Valida que o client_token pertence a um posto ativo
    async with db.execute(
        "SELECT id FROM clientes WHERE token = ? AND ativo = 1", (body.client_token,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=403, detail="Token de cliente inválido.")

    username = body.username.strip().lower()
    async with db.execute(
        "SELECT id, username, password_hash FROM usuarios WHERE username = ?", (username,)
    ) as cur:
        row = await cur.fetchone()

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")

    return {"ok": True, "username": row["username"], "uid": row["id"]}


# ── Consulta pública de menus (usada pelo app de envio) ───────────────────────
@router.get("/usuario-menus/{username}")
async def get_usuario_menus_publico(
    username: str,
    client_token: str = Query(..., description="Token do cliente (posto)"),
    db=Depends(get_db),
):
    """Retorna os menus permitidos para um usuário. Autenticado pelo token do posto."""
    # Valida que o client_token pertence a um posto ativo
    async with db.execute(
        "SELECT id FROM clientes WHERE token = ? AND ativo = 1", (client_token,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=403, detail="Token de cliente inválido.")

    async with db.execute(
        "SELECT menus FROM usuarios WHERE username = ?", (username.lower(),)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        # Usuário não encontrado no monitor → permite todos os menus
        return {"menus": None, "all_allowed": True}

    menus_raw = row["menus"]
    if menus_raw is None:
        return {"menus": None, "all_allowed": True}

    try:
        menus = json.loads(menus_raw)
    except Exception:
        return {"menus": None, "all_allowed": True}

    return {"menus": menus, "all_allowed": False}


# ── Trocar username ───────────────────────────────────────────────────────────
class UsernameUpdate(BaseModel):
    username: str


@router.put("/usuarios/{usuario_id}/username")
async def change_username(
    usuario_id: int,
    body: UsernameUpdate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    username = body.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username inválido.")
    async with db.execute("SELECT username FROM usuarios WHERE id=?", (usuario_id,)) as cur:
        row = await cur.fetchone()
    try:
        await db.execute(
            "UPDATE usuarios SET username=? WHERE id=?",
            (username, usuario_id),
        )
        await db.commit()
        if row and row["username"] != username:
            await _app_sync_username(row["username"], username)
        return {"ok": True}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Username já está em uso.")


@router.get("/cliente/{token}")
async def setup_cliente(token: str, db=Depends(get_db)):
    """Retorna dados de configuração para o cliente (posto) que faz setup inicial."""
    async with db.execute(
        "SELECT id, nome, cnpj, token FROM clientes WHERE token = ? AND ativo = 1", (token,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Token não encontrado")
    return {"id": row["id"], "nome": row["nome"], "cnpj": row["cnpj"], "token": row["token"]}


# ══════════════════════════════════════════════════════════════════════════════
#  ADMINS DO MONITOR — CRUD
#  Contas separadas que acessam APENAS o painel monitor (não sincronizadas com app)
# ══════════════════════════════════════════════════════════════════════════════

class AdminCreate(BaseModel):
    username: str
    password: str


class SenhaUpdate(BaseModel):
    password: str


@router.get("/admins")
async def list_admins(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        "SELECT id, username, created_at FROM admins ORDER BY username"
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/admins", status_code=status.HTTP_201_CREATED)
async def create_admin(
    body: AdminCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    username = body.username.strip().lower()
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Senha precisa ter pelo menos 6 caracteres.")
    try:
        cur = await db.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (username, hash_password(body.password)),
        )
        await db.commit()
        return {"id": cur.lastrowid, "username": username}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Usuário já existe.")


@router.put("/admins/{admin_id}/senha")
async def update_admin_senha(
    admin_id: int,
    body: SenhaUpdate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Senha precisa ter pelo menos 6 caracteres.")
    await db.execute(
        "UPDATE admins SET password_hash=? WHERE id=?",
        (hash_password(body.password), admin_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin(
    admin_id: int,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # Não pode deletar a si mesmo
    if admin_id == user.get("uid"):
        raise HTTPException(status_code=400, detail="Não é possível remover seu próprio usuário.")
    await db.execute("DELETE FROM admins WHERE id=?", (admin_id,))
    await db.commit()
