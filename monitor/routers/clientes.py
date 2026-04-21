import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/clientes", tags=["clientes"])


class ClienteCreate(BaseModel):
    nome: str
    cnpj: Optional[str] = None
    representante_id: Optional[int] = None
    endereco: Optional[str] = None
    cidade: Optional[str] = None
    uf: Optional[str] = None


class ClienteUpdate(ClienteCreate):
    ativo: Optional[int] = 1


@router.get("")
async def list_clientes(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        """SELECT c.id, c.nome, c.cnpj, c.token, c.ativo, c.versao_instalada,
                  c.cidade, c.uf, c.created_at,
                  r.nome as rep_nome
           FROM clientes c
           LEFT JOIN representantes r ON r.id = c.representante_id
           ORDER BY c.nome"""
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cliente(
    body: ClienteCreate,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    token = secrets.token_urlsafe(24)
    cur = await db.execute(
        """INSERT INTO clientes (nome, cnpj, token, representante_id, endereco, cidade, uf)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (body.nome, body.cnpj, token, body.representante_id, body.endereco, body.cidade, body.uf),
    )
    await db.commit()
    return {"id": cur.lastrowid, "token": token, "nome": body.nome}


@router.put("/{cliente_id}")
async def update_cliente(
    cliente_id: int,
    body: ClienteUpdate,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        """UPDATE clientes SET nome=?, cnpj=?, representante_id=?, endereco=?, cidade=?, uf=?, ativo=?
           WHERE id=?""",
        (body.nome, body.cnpj, body.representante_id, body.endereco, body.cidade, body.uf, body.ativo, cliente_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cliente(
    cliente_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
    await db.commit()
