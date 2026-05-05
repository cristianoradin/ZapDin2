from fastapi import APIRouter, Depends

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config(
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    async with db.execute(
        "SELECT key, value FROM config WHERE empresa_id=?", (empresa_id,)
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.post("")
async def set_config(
    body: dict,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    for key, value in body.items():
        await db.execute(
            """INSERT INTO config (empresa_id, key, value) VALUES (?, ?, ?)
               ON CONFLICT (empresa_id, key) DO UPDATE SET value = EXCLUDED.value""",
            (empresa_id, key, str(value)),
        )
    await db.commit()
    return {"ok": True}
