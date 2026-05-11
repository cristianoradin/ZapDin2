import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel


from ..core.database import get_db
from ..core.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitor"])

ACTIVE_THRESHOLD_MINUTES = 3


class HeartbeatPayload(BaseModel):
    nome: Optional[str] = None
    cnpj: Optional[str] = None
    versao: Optional[str] = None
    porta: Optional[int] = None
    wa_status: Optional[str] = None  # connected | qr_code | disconnected


@router.post("/api/report")
async def receive_heartbeat(
    body: HeartbeatPayload,
    request: Request,
    x_client_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    if not x_client_token:
        raise HTTPException(status_code=401, detail="Token obrigatório")

    async with db.execute(
        "SELECT id FROM clientes WHERE token = ? AND ativo = 1", (x_client_token,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Token inválido")

    cliente_id = row["id"]
    client_ip = request.client.host if request.client else None

    # Update version info on cliente
    if body.versao:
        await db.execute(
            "UPDATE clientes SET versao_instalada = ? WHERE id = ?", (body.versao, cliente_id)
        )

    wa_st = body.wa_status if body.wa_status in ("connected", "qr_code", "disconnected") else "disconnected"

    # Tenta INSERT com wa_status; se coluna não existir (migration pendente), cai no fallback
    try:
        await db.execute(
            "INSERT INTO heartbeats (cliente_id, versao, ip, wa_status) VALUES (?, ?, ?, ?)",
            (cliente_id, body.versao, client_ip, wa_st),
        )
    except Exception:
        # Coluna wa_status ainda não existe — usa INSERT antigo e roda migration agora
        try:
            await db.execute(
                "ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS wa_status TEXT DEFAULT 'disconnected'"
            )
        except Exception:
            pass
        await db.execute(
            "INSERT INTO heartbeats (cliente_id, versao, ip) VALUES (?, ?, ?)",
            (cliente_id, body.versao, client_ip),
        )
        logger.warning("Heartbeat sem wa_status — migration aplicada agora")

    await db.commit()
    return {"ok": True}


@router.get("/api/monitor")
async def get_monitor(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    # PostgreSQL: created_at é TIMESTAMPTZ, comparação feita diretamente no SQL
    async with db.execute(
        """SELECT c.id, c.nome, c.cnpj, c.versao_instalada, c.cidade, c.uf,
                  (SELECT created_at FROM heartbeats WHERE cliente_id = c.id ORDER BY created_at DESC LIMIT 1) as ultimo_ping,
                  (SELECT ip        FROM heartbeats WHERE cliente_id = c.id ORDER BY created_at DESC LIMIT 1) as ultimo_ip,
                  COALESCE((SELECT wa_status FROM heartbeats WHERE cliente_id = c.id ORDER BY created_at DESC LIMIT 1), 'disconnected') as wa_status
           FROM clientes c
           WHERE c.ativo = 1
           ORDER BY c.nome"""
    ) as cur:
        rows = await cur.fetchall()

    threshold = datetime.now(tz=timezone.utc) - timedelta(minutes=ACTIVE_THRESHOLD_MINUTES)

    result = []
    for r in rows:
        row_dict = dict(r)
        # ultimo_ping é datetime com tz (TIMESTAMPTZ); converte para string ISO para o frontend
        if row_dict.get("ultimo_ping"):
            row_dict["ultimo_ping"] = row_dict["ultimo_ping"].isoformat()
        row_dict["ativo"] = bool(r["ultimo_ping"] and r["ultimo_ping"] >= threshold)
        result.append(row_dict)

    return result
