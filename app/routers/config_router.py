from fastapi import APIRouter, Depends
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute("SELECT key, value FROM config") as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.post("")
async def set_config(
    body: dict,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    for key, value in body.items():
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value))
        )
    await db.commit()
    return {"ok": True}
