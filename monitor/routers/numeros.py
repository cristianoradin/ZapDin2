"""
Rota para registrar e listar números WhatsApp contactados por cada cliente.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["numeros"])


class RegistrarNumeroPayload(BaseModel):
    phone: str
    nome: Optional[str] = ""
    tipo: Optional[str] = "text"  # text | file


@router.post("/api/numeros/registrar")
async def registrar_numero(
    body: RegistrarNumeroPayload,
    x_client_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    """Chamado pelo app após cada envio bem-sucedido."""
    if not x_client_token:
        raise HTTPException(status_code=401, detail="Token obrigatório")

    async with db.execute(
        "SELECT id FROM clientes WHERE token = ? AND ativo = 1", (x_client_token,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Token inválido")

    cliente_id = row["id"]
    phone = "".join(c for c in (body.phone or "") if c.isdigit())
    nome = (body.nome or "").strip()

    if not phone:
        raise HTTPException(status_code=400, detail="Telefone inválido")

    # UPSERT: incrementa contador ou insere novo
    await db.execute(
        """INSERT INTO numeros_wa (cliente_id, phone, nome, total_enviados, ultima_mensagem)
           VALUES (?, ?, ?, 1, NOW())
           ON CONFLICT (cliente_id, phone) DO UPDATE
           SET total_enviados  = numeros_wa.total_enviados + 1,
               ultima_mensagem = NOW(),
               nome            = CASE WHEN EXCLUDED.nome != '' THEN EXCLUDED.nome
                                      ELSE numeros_wa.nome END""",
        (cliente_id, phone, nome),
    )
    await db.commit()
    logger.info("Número registrado: cliente=%s phone=%s nome=%s", cliente_id, phone, nome)
    return {"ok": True}


@router.get("/api/numeros")
async def listar_numeros(
    q: Optional[str] = None,
    _: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Lista todos os números, com filtro opcional por nome/telefone."""
    if q and q.strip():
        term = f"%{q.strip()}%"
        async with db.execute(
            """SELECT n.phone, n.nome, n.total_enviados, n.ultima_mensagem,
                      c.nome as cliente_nome
               FROM numeros_wa n
               JOIN clientes c ON c.id = n.cliente_id
               WHERE n.phone ILIKE ? OR n.nome ILIKE ? OR c.nome ILIKE ?
               ORDER BY n.ultima_mensagem DESC""",
            (term, term, term),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            """SELECT n.phone, n.nome, n.total_enviados, n.ultima_mensagem,
                      c.nome as cliente_nome
               FROM numeros_wa n
               JOIN clientes c ON c.id = n.cliente_id
               ORDER BY n.ultima_mensagem DESC""",
        ) as cur:
            rows = await cur.fetchall()

    result = []
    for r in rows:
        d = dict(r)
        if d.get("ultima_mensagem"):
            d["ultima_mensagem"] = d["ultima_mensagem"].isoformat()
        result.append(d)
    return result


@router.get("/api/numeros/stats")
async def stats_numeros(
    _: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    async with db.execute(
        "SELECT COUNT(*) as total, SUM(total_enviados) as total_enviados FROM numeros_wa"
    ) as cur:
        row = await cur.fetchone()
    return {
        "total_numeros": row["total"] if row else 0,
        "total_enviados": row["total_enviados"] if row else 0,
    }
