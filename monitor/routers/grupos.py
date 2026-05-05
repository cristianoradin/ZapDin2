from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import asyncpg

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/grupos", tags=["grupos"])


class GrupoCreate(BaseModel):
    nome: str
    descricao: Optional[str] = None


@router.get("")
async def list_grupos(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        """SELECT g.id, g.nome, g.descricao, g.created_at,
                  COUNT(c.id) as total_clientes
           FROM grupos g
           LEFT JOIN clientes c ON c.grupo_id = g.id
           GROUP BY g.id
           ORDER BY g.nome"""
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_grupo(
    body: GrupoCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")
    try:
        cur = await db.execute(
            "INSERT INTO grupos (nome, descricao) VALUES (?, ?)",
            (nome, body.descricao),
        )
        await db.commit()
        return {"id": cur.lastrowid, "nome": nome}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Já existe um grupo com este nome.")


@router.put("/{grupo_id}")
async def update_grupo(
    grupo_id: int,
    body: GrupoCreate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")
    try:
        await db.execute(
            "UPDATE grupos SET nome=?, descricao=? WHERE id=?",
            (nome, body.descricao, grupo_id),
        )
        await db.commit()
        return {"ok": True}
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Já existe um grupo com este nome.")


@router.delete("/{grupo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grupo(
    grupo_id: int,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    # Remove vínculo dos clientes antes de deletar o grupo
    await db.execute("UPDATE clientes SET grupo_id=NULL WHERE grupo_id=?", (grupo_id,))
    await db.execute("DELETE FROM grupos WHERE id=?", (grupo_id,))
    await db.commit()
