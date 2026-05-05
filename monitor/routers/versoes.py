from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional


from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/versao", tags=["versoes"])


class VersaoUpdate(BaseModel):
    versao: str
    url_download: Optional[str] = None
    notas: Optional[str] = None


@router.get("/whatsapp")
async def get_versao_whatsapp(db=Depends(get_db)):
    """Público — consultado pelos postos para checar atualização."""
    async with db.execute("SELECT versao, url_download, notas FROM versoes WHERE app = 'whatsapp'") as cur:
        row = await cur.fetchone()
    if not row:
        return {"versao": "1.0.0"}
    return dict(row)


@router.post("/whatsapp")
async def set_versao_whatsapp(
    body: VersaoUpdate,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        """INSERT INTO versoes (app, versao, url_download, notas, updated_at)
           VALUES ('whatsapp', ?, ?, ?, NOW())
           ON CONFLICT(app) DO UPDATE SET versao=EXCLUDED.versao,
               url_download=EXCLUDED.url_download, notas=EXCLUDED.notas, updated_at=NOW()""",
        (body.versao, body.url_download, body.notas),
    )
    await db.commit()
    return {"ok": True}
