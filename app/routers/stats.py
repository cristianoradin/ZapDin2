from fastapi import APIRouter, Depends

from ..core.config import settings
from ..core.database import get_db
from ..core.security import get_current_user

if settings.use_evolution:
    from ..services.evolution_service import evo_manager as wa_manager
else:
    from ..services.whatsapp_service import wa_manager

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats(
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]

    async with db.execute(
        "SELECT COUNT(*) as total FROM mensagens WHERE empresa_id=?", (empresa_id,)
    ) as cur:
        total = (await cur.fetchone())["total"]

    async with db.execute(
        "SELECT COUNT(*) as total FROM mensagens WHERE empresa_id=? AND status='sent'",
        (empresa_id,),
    ) as cur:
        enviadas = (await cur.fetchone())["total"]

    async with db.execute(
        "SELECT COUNT(*) as total FROM mensagens WHERE empresa_id=? AND status='failed'",
        (empresa_id,),
    ) as cur:
        falhas = (await cur.fetchone())["total"]

    async with db.execute(
        "SELECT COUNT(*) as total FROM mensagens WHERE empresa_id=? AND created_at::date = CURRENT_DATE",
        (empresa_id,),
    ) as cur:
        hoje = (await cur.fetchone())["total"]

    sessoes_ativas = sum(
        1 for s in wa_manager.get_status(empresa_id) if s["status"] == "connected"
    )

    async with db.execute(
        """SELECT destinatario, mensagem, status, created_at
           FROM mensagens WHERE empresa_id=?
           ORDER BY created_at DESC LIMIT 20""",
        (empresa_id,),
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
