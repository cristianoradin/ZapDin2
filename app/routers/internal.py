"""
ZapDin — Rotas Internas (Worker → App / Monitor → App)
========================================
Acessíveis APENAS de 127.0.0.1. Sem autenticação JWT — protegidas por IP.

Endpoints:
  GET    /internal/queue/peek               → próximo item na fila (sem removê-lo)
  POST   /internal/queue/dispatch           → executa o envio de um item específico
  GET    /internal/sessions/status          → status das sessões WA (para o worker)
  GET    /internal/sessions/pick            → retorna uma sessão conectada (round-robin)

  -- Sincronização de usuários (Monitor → App) --
  POST   /internal/usuarios/sync            → cria ou atualiza usuário (upsert)
  DELETE /internal/usuarios/{username}      → remove usuário
  PUT    /internal/usuarios/{username}/senha → troca senha
  PUT    /internal/usuarios/{username}/username → renomeia usuário
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import hash_password
from ..services.whatsapp_service import wa_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])

UPLOAD_DIR = "data/arquivos"


# ─────────────────────────────────────────────────────────────────────────────
#  Sync de usuários — chamado pelo Monitor
# ─────────────────────────────────────────────────────────────────────────────

class UserSyncPayload(BaseModel):
    username: str
    password: str   # senha em texto plano — será hasheada aqui


class SenhaPayload(BaseModel):
    password: str


class UsernamePayload(BaseModel):
    username: str


@router.post("/usuarios/sync")
async def sync_usuario(
    body: UserSyncPayload,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Cria ou atualiza (upsert) um usuário no banco do app."""
    _require_localhost(request)
    hashed = hash_password(body.password)
    await db.execute(
        """INSERT INTO usuarios (username, password_hash)
           VALUES (?, ?)
           ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash""",
        (body.username.strip().lower(), hashed),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/usuarios/{username}")
async def delete_usuario(
    username: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Remove um usuário do banco do app."""
    _require_localhost(request)
    await db.execute("DELETE FROM usuarios WHERE username = ?", (username.lower(),))
    await db.commit()
    return {"ok": True}


@router.put("/usuarios/{username}/senha")
async def change_senha_usuario(
    username: str,
    body: SenhaPayload,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Troca a senha de um usuário no banco do app."""
    _require_localhost(request)
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
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Renomeia um usuário no banco do app."""
    _require_localhost(request)
    try:
        await db.execute(
            "UPDATE usuarios SET username=? WHERE username=?",
            (body.username.strip().lower(), username.lower()),
        )
        await db.commit()
    except Exception:
        pass  # conflict de username — ignora silenciosamente
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Guard: rejeita requisições fora do localhost
# ─────────────────────────────────────────────────────────────────────────────

def _require_localhost(request: Request) -> None:
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        logger.warning("[internal] Acesso negado de %s", client_ip)
        raise HTTPException(status_code=403, detail="Acesso restrito ao host local.")


# ─────────────────────────────────────────────────────────────────────────────
#  Modelos
# ─────────────────────────────────────────────────────────────────────────────

class DispatchPayload(BaseModel):
    item_type: str          # "text" | "file"
    item_id: int
    sessao_id: str
    processed_content: str  # mensagem após spintax (texto) ou caption (arquivo)


class DispatchResult(BaseModel):
    ok: bool
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
#  Rotas
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/queue/peek")
async def peek_queue(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Retorna o próximo item da fila sem removê-lo.
    O worker aplica delays e anti-ban ANTES de chamar /dispatch.
    """
    _require_localhost(request)

    # Prioridade: mensagens de texto antes de arquivos
    async with db.execute(
        "SELECT id, destinatario, mensagem FROM mensagens "
        "WHERE status='queued' ORDER BY id LIMIT 1"
    ) as cur:
        msg = await cur.fetchone()

    if msg:
        return {"type": "text", "id": msg["id"], "phone": msg["destinatario"], "content": msg["mensagem"]}

    async with db.execute(
        "SELECT id, destinatario, nome_arquivo, nome_original, caption "
        "FROM arquivos WHERE status='queued' ORDER BY id LIMIT 1"
    ) as cur:
        arq = await cur.fetchone()

    if arq:
        return {
            "type": "file",
            "id": arq["id"],
            "phone": arq["destinatario"],
            "nome_arquivo": arq["nome_arquivo"],
            "nome_original": arq["nome_original"],
            "content": arq["caption"] or "",
        }

    return {"type": None}


@router.post("/queue/dispatch")
async def dispatch_item(
    body: DispatchPayload,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> DispatchResult:
    """
    Executa o envio de um item (o worker já aplicou delay e spintax).
    Atualiza status no banco e retorna resultado.
    """
    _require_localhost(request)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if body.item_type == "text":
        ok, err = await wa_manager.send_text(body.sessao_id, "", body.processed_content)
        # Precisamos do telefone — buscamos no banco
        async with db.execute("SELECT destinatario FROM mensagens WHERE id=?", (body.item_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return DispatchResult(ok=False, error="Mensagem não encontrada no banco.")
        ok, err = await wa_manager.send_text(body.sessao_id, row["destinatario"], body.processed_content)
        status = "sent" if ok else "failed"
        await db.execute(
            "UPDATE mensagens SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=?",
            (status, body.sessao_id, now if ok else None, err, body.item_id),
        )
        await db.commit()
        return DispatchResult(ok=ok, error=err)

    if body.item_type == "file":
        async with db.execute(
            "SELECT destinatario, nome_arquivo, nome_original FROM arquivos WHERE id=?",
            (body.item_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return DispatchResult(ok=False, error="Arquivo não encontrado no banco.")

        file_path = os.path.join(UPLOAD_DIR, row["nome_arquivo"])
        if not os.path.exists(file_path):
            await db.execute(
                "UPDATE arquivos SET status='failed', erro='Arquivo não encontrado no disco' WHERE id=?",
                (body.item_id,),
            )
            await db.commit()
            return DispatchResult(ok=False, error="Arquivo não encontrado no disco.")

        ok, err = await wa_manager.send_file(
            body.sessao_id,
            row["destinatario"],
            file_path,
            row["nome_original"],
            body.processed_content or None,
        )
        status = "sent" if ok else "failed"
        await db.execute(
            "UPDATE arquivos SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=?",
            (status, body.sessao_id, now if ok else None, err, body.item_id),
        )
        await db.commit()

        if ok:
            wa_manager.schedule_status_check(body.item_id, body.sessao_id, row["destinatario"])

        return DispatchResult(ok=ok, error=err)

    return DispatchResult(ok=False, error=f"Tipo desconhecido: {body.item_type}")


@router.get("/sessions/pick")
async def pick_session(request: Request):
    """Retorna uma sessão conectada disponível (round-robin)."""
    _require_localhost(request)
    sessao_id = wa_manager.pick_session()
    if not sessao_id:
        return {"sessao_id": None, "available": False}
    return {"sessao_id": sessao_id, "available": True}


@router.get("/sessions/status")
async def sessions_status(request: Request):
    """Lista todas as sessões e seus status (para dashboard do worker)."""
    _require_localhost(request)
    return {"sessions": wa_manager.get_status()}


@router.get("/daily-count/{sessao_id}")
async def daily_count(
    sessao_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Total de envios hoje para uma sessão (usado pelo worker para limite diário)."""
    _require_localhost(request)

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM mensagens "
        "WHERE sessao_id=? AND status='sent' AND date(sent_at)=date('now')",
        (sessao_id,),
    ) as cur:
        msg_cnt = (await cur.fetchone())["cnt"]

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM arquivos "
        "WHERE sessao_id=? AND status='sent' AND date(sent_at)=date('now')",
        (sessao_id,),
    ) as cur:
        arq_cnt = (await cur.fetchone())["cnt"]

    return {"sessao_id": sessao_id, "total_today": msg_cnt + arq_cnt}
