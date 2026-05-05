from fastapi import APIRouter, Depends

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/arquivos", tags=["arquivos"])


@router.get("")
async def list_arquivos(
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    async with db.execute(
        """SELECT id, nome_original, tamanho, destinatario, status,
                  created_at, sent_at, delivered_at, read_at
           FROM arquivos
           WHERE empresa_id=?
           ORDER BY created_at DESC LIMIT 100""",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
