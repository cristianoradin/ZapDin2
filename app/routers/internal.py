"""
ZapDin — Rotas Internas (Worker → App)
========================================
Acessíveis APENAS de 127.0.0.1. Sem autenticação JWT — protegidas por IP.

Endpoints:
  GET    /internal/queue/peek                   → próximo item na fila (inclui empresa_id)
  POST   /internal/queue/dispatch               → executa o envio de um item específico
  GET    /internal/sessions/status?empresa_id=  → status das sessões WA (para o worker)
  GET    /internal/sessions/pick?empresa_id=    → retorna uma sessão conectada (round-robin)
  GET    /internal/daily-count/{sessao_id}?empresa_id= → envios hoje por sessão/empresa
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import hash_password
from ..services.whatsapp_service import wa_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])

UPLOAD_DIR = "data/arquivos"


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
    empresa_id: int
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
    db=Depends(get_db),
):
    """
    Retorna o próximo item da fila sem removê-lo.
    Inclui empresa_id para que o worker saiba qual sessão usar.
    """
    _require_localhost(request)

    # Prioridade: mensagens de texto antes de arquivos
    async with db.execute(
        "SELECT id, empresa_id, destinatario, mensagem FROM mensagens "
        "WHERE status='queued' ORDER BY id LIMIT 1"
    ) as cur:
        msg = await cur.fetchone()

    if msg:
        return {
            "type": "text",
            "id": msg["id"],
            "empresa_id": msg["empresa_id"],
            "phone": msg["destinatario"],
            "content": msg["mensagem"],
        }

    async with db.execute(
        "SELECT id, empresa_id, destinatario, nome_arquivo, nome_original, caption "
        "FROM arquivos WHERE status='queued' ORDER BY id LIMIT 1"
    ) as cur:
        arq = await cur.fetchone()

    if arq:
        return {
            "type": "file",
            "id": arq["id"],
            "empresa_id": arq["empresa_id"],
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
    db=Depends(get_db),
) -> DispatchResult:
    """
    Executa o envio de um item (o worker já aplicou delay e spintax).
    """
    _require_localhost(request)

    now = datetime.now()

    if body.item_type == "text":
        async with db.execute(
            "SELECT destinatario FROM mensagens WHERE id=? AND empresa_id=?",
            (body.item_id, body.empresa_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return DispatchResult(ok=False, error="Mensagem não encontrada no banco.")
        ok, err = await wa_manager.send_text(
            body.sessao_id, body.empresa_id, row["destinatario"], body.processed_content
        )
        st = "sent" if ok else "failed"
        await db.execute(
            "UPDATE mensagens SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=? AND empresa_id=?",
            (st, body.sessao_id, now if ok else None, err, body.item_id, body.empresa_id),
        )
        await db.commit()
        return DispatchResult(ok=ok, error=err)

    if body.item_type == "file":
        async with db.execute(
            "SELECT destinatario, nome_arquivo, nome_original FROM arquivos WHERE id=? AND empresa_id=?",
            (body.item_id, body.empresa_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return DispatchResult(ok=False, error="Arquivo não encontrado no banco.")

        file_path = os.path.join(UPLOAD_DIR, row["nome_arquivo"])
        if not os.path.exists(file_path):
            await db.execute(
                "UPDATE arquivos SET status='failed', erro='Arquivo não encontrado no disco' WHERE id=? AND empresa_id=?",
                (body.item_id, body.empresa_id),
            )
            await db.commit()
            return DispatchResult(ok=False, error="Arquivo não encontrado no disco.")

        ok, err = await wa_manager.send_file(
            body.sessao_id,
            body.empresa_id,
            row["destinatario"],
            file_path,
            row["nome_original"],
            body.processed_content or None,
        )
        st = "sent" if ok else "failed"
        await db.execute(
            "UPDATE arquivos SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=? AND empresa_id=?",
            (st, body.sessao_id, now if ok else None, err, body.item_id, body.empresa_id),
        )
        await db.commit()

        if ok:
            wa_manager.schedule_status_check(
                body.item_id, body.sessao_id, body.empresa_id, row["destinatario"]
            )

        return DispatchResult(ok=ok, error=err)

    return DispatchResult(ok=False, error=f"Tipo desconhecido: {body.item_type}")


@router.get("/sessions/pick")
async def pick_session(
    request: Request,
    empresa_id: int = Query(...),
):
    """Retorna uma sessão conectada disponível para a empresa (round-robin)."""
    _require_localhost(request)
    sessao_id = wa_manager.pick_session(empresa_id)
    if not sessao_id:
        return {"sessao_id": None, "available": False}
    return {"sessao_id": sessao_id, "available": True}


@router.get("/sessions/status")
async def sessions_status(
    request: Request,
    empresa_id: int = Query(...),
):
    """Lista sessões e status para uma empresa específica."""
    _require_localhost(request)
    return {"sessions": wa_manager.get_status(empresa_id)}


@router.get("/daily-count/{sessao_id}")
async def daily_count(
    sessao_id: str,
    request: Request,
    empresa_id: int = Query(...),
    db=Depends(get_db),
):
    """Total de envios hoje para uma sessão/empresa (usado pelo worker para limite diário)."""
    _require_localhost(request)

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM mensagens "
        "WHERE sessao_id=? AND empresa_id=? AND status='sent' AND sent_at::date = CURRENT_DATE",
        (sessao_id, empresa_id),
    ) as cur:
        msg_cnt = (await cur.fetchone())["cnt"]

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM arquivos "
        "WHERE sessao_id=? AND empresa_id=? AND status='sent' AND sent_at::date = CURRENT_DATE",
        (sessao_id, empresa_id),
    ) as cur:
        arq_cnt = (await cur.fetchone())["cnt"]

    return {"sessao_id": sessao_id, "empresa_id": empresa_id, "total_today": msg_cnt + arq_cnt}
