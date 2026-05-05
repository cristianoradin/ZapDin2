import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel


from ..core.config import settings
from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/clientes", tags=["clientes"])


class ClienteCreate(BaseModel):
    nome: str
    cnpj: Optional[str] = None
    representante_id: Optional[int] = None
    grupo_id: Optional[int] = None
    endereco: Optional[str] = None
    cidade: Optional[str] = None
    uf: Optional[str] = None


class ClienteUpdate(ClienteCreate):
    ativo: Optional[int] = 1


@router.get("")
async def list_clientes(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        """SELECT c.id, c.nome, c.cnpj, c.token, c.ativo, c.versao_instalada,
                  c.cidade, c.uf, c.created_at, c.activation_token,
                  c.grupo_id,
                  g.nome as grupo_nome,
                  r.nome as rep_nome
           FROM clientes c
           LEFT JOIN grupos g ON g.id = c.grupo_id
           LEFT JOIN representantes r ON r.id = c.representante_id
           ORDER BY g.nome NULLS LAST, c.nome"""
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cliente(
    body: ClienteCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    token = secrets.token_urlsafe(24)
    cur = await db.execute(
        """INSERT INTO clientes (nome, cnpj, token, representante_id, grupo_id, endereco, cidade, uf)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.nome, body.cnpj, token, body.representante_id, body.grupo_id,
         body.endereco, body.cidade, body.uf),
    )
    await db.commit()
    return {"id": cur.lastrowid, "token": token, "nome": body.nome}


@router.put("/{cliente_id}")
async def update_cliente(
    cliente_id: int,
    body: ClienteUpdate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        """UPDATE clientes SET nome=?, cnpj=?, representante_id=?, grupo_id=?,
                               endereco=?, cidade=?, uf=?, ativo=?
           WHERE id=?""",
        (body.nome, body.cnpj, body.representante_id, body.grupo_id,
         body.endereco, body.cidade, body.uf, body.ativo, cliente_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cliente(
    cliente_id: int,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
    await db.commit()


@router.get("/{cliente_id}/usuarios")
async def get_usuarios_do_posto(
    cliente_id: int,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Busca usuários do app instalado no posto via API interna do app."""
    async with db.execute(
        "SELECT token, nome FROM clientes WHERE id = ?", (cliente_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Posto não encontrado")

    # O app expõe /api/internal/usuarios com autenticação pelo token do cliente
    app_url = settings.app_url.rstrip("/") if hasattr(settings, "app_url") and settings.app_url else None
    if not app_url:
        raise HTTPException(status_code=503, detail="app_url não configurada no monitor")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{app_url}/api/monitor-sync/usuarios",
                headers={"x-monitor-token": row["token"]},
            )
        if resp.status_code == 200:
            return resp.json()
        raise HTTPException(status_code=resp.status_code, detail="Erro ao buscar usuários do posto")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Posto offline ou inacessível")
