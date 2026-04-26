from fastapi import APIRouter, Depends
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user
from ..services.whatsapp_service import wa_manager

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute("SELECT COUNT(*) as total FROM mensagens") as cur:
        total = (await cur.fetchone())["total"]

    async with db.execute("SELECT COUNT(*) as total FROM mensagens WHERE status = 'sent'") as cur:
        enviadas = (await cur.fetchone())["total"]

    async with db.execute("SELECT COUNT(*) as total FROM mensagens WHERE status = 'failed'") as cur:
        falhas = (await cur.fetchone())["total"]

    async with db.execute(
        "SELECT COUNT(*) as total FROM mensagens WHERE date(created_at) = date('now')"
    ) as cur:
        hoje = (await cur.fetchone())["total"]

    sessoes_ativas = sum(
        1 for s in wa_manager.get_status() if s["status"] == "connected"
    )

    async with db.execute(
        "SELECT destinatario, mensagem, status, created_at FROM mensagens ORDER BY created_at DESC LIMIT 20"
    ) as cur:
        recentes = [dict(r) for r in await cur.fetchall()]

    return {
        "total_mensagens": total,
        "enviadas": enviadas,
        "falhas": falhas,
        "hoje": hoje,
        "sessoes_ativas": sessoes_ativas,
        "recentes": recentes,
    }
