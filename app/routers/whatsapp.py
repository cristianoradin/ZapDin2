from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user
from ..services.whatsapp_service import wa_manager

router = APIRouter(prefix="/api/sessoes", tags=["whatsapp"])


class SessaoCreate(BaseModel):
    nome: str


@router.get("")
async def list_sessoes(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute("SELECT id, nome, status, phone, last_seen FROM sessoes_wa ORDER BY created_at") as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_sessao(
    body: SessaoCreate,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    import uuid
    sessao_id = str(uuid.uuid4())[:8]
    await db.execute(
        "INSERT INTO sessoes_wa (id, nome, status) VALUES (?, ?, 'disconnected')",
        (sessao_id, body.nome),
    )
    await db.commit()
    await wa_manager.add_session(sessao_id, body.nome)
    return {"id": sessao_id, "nome": body.nome, "status": "disconnected"}


@router.delete("/{sessao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sessao(
    sessao_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await wa_manager.remove_session(sessao_id)
    await db.execute("DELETE FROM sessoes_wa WHERE id = ?", (sessao_id,))
    await db.commit()


@router.get("/live-status")
async def live_status(_: dict = Depends(get_current_user)):
    return wa_manager.get_status()


@router.get("/qr/{sessao_id}")
async def get_qr(sessao_id: str, _: dict = Depends(get_current_user)):
    qr = wa_manager.get_qr(sessao_id)
    if qr is None:
        raise HTTPException(status_code=404, detail="QR não disponível")
    return {"qr": qr}
