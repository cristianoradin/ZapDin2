from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(tags=["monitor"])

ACTIVE_THRESHOLD_MINUTES = 3


class HeartbeatPayload(BaseModel):
    nome: Optional[str] = None
    cnpj: Optional[str] = None
    versao: Optional[str] = None
    porta: Optional[int] = None


@router.post("/api/report")
async def receive_heartbeat(
    body: HeartbeatPayload,
    request: Request,
    x_client_token: Optional[str] = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
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

    await db.execute(
        "INSERT INTO heartbeats (cliente_id, versao, ip) VALUES (?, ?, ?)",
        (cliente_id, body.versao, client_ip),
    )
    await db.commit()
    return {"ok": True}


@router.get("/api/monitor")
async def get_monitor(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    # SQLite armazena datetime('now') como "YYYY-MM-DD HH:MM:SS" (espaço, sem micros).
    # isoformat() usa "T" como separador, que tem ASCII 84 > espaço (32), quebrando a
    # comparação de string — todo cliente apareceria offline. Usamos strftime para
    # gerar o mesmo formato que o SQLite usa.
    threshold = (datetime.utcnow() - timedelta(minutes=ACTIVE_THRESHOLD_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

    async with db.execute(
        """SELECT c.id, c.nome, c.cnpj, c.versao_instalada, c.cidade, c.uf,
                  (SELECT created_at FROM heartbeats WHERE cliente_id = c.id ORDER BY created_at DESC LIMIT 1) as ultimo_ping,
                  (SELECT ip FROM heartbeats WHERE cliente_id = c.id ORDER BY created_at DESC LIMIT 1) as ultimo_ip
           FROM clientes c
           WHERE c.ativo = 1
           ORDER BY c.nome"""
    ) as cur:
        rows = await cur.fetchall()

    result = []
    for r in rows:
        row_dict = dict(r)
        row_dict["ativo"] = bool(r["ultimo_ping"] and r["ultimo_ping"] >= threshold)
        result.append(row_dict)

    return result
