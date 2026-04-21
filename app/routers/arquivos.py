from fastapi import APIRouter, Depends
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/arquivos", tags=["arquivos"])


@router.get("")
async def list_arquivos(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute(
        "SELECT id, nome_original, tamanho, destinatario, status, created_at FROM arquivos ORDER BY created_at DESC LIMIT 100"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
