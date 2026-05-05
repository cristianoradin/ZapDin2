from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user
from ..services import telegram_service

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str


@router.get("/config")
async def get_config(
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    async with db.execute(
        "SELECT key, value FROM config WHERE empresa_id=? AND key IN ('tg_bot_token','tg_chat_id')",
        (empresa_id,),
    ) as cur:
        rows = await cur.fetchall()
    data = {r["key"]: r["value"] for r in rows}
    return {
        "bot_token": data.get("tg_bot_token", ""),
        "chat_id": data.get("tg_chat_id", ""),
        "configured": telegram_service.is_configured(),
    }


@router.post("/config")
async def save_config(
    body: TelegramConfig,
    db=Depends(get_db),
    user: dict = Depends(get_current_user),
):
    empresa_id = user["empresa_id"]
    _upsert = (
        "INSERT INTO config (empresa_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT (empresa_id, key) DO UPDATE SET value = EXCLUDED.value"
    )
    await db.execute(_upsert, (empresa_id, 'tg_bot_token', body.bot_token))
    await db.execute(_upsert, (empresa_id, 'tg_chat_id', body.chat_id))
    await db.commit()
    telegram_service.configure(body.bot_token, body.chat_id)
    return {"ok": True}


@router.post("/test")
async def test_message(_: dict = Depends(get_current_user)):
    if not telegram_service.is_configured():
        raise HTTPException(status_code=400, detail="Configure o Bot Token e o Chat ID primeiro.")
    ok = await telegram_service.send(
        "✅ <b>ZapDin — Teste de Conexão</b>\n\nSua integração com o Telegram está funcionando corretamente!"
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Falha ao enviar mensagem. Verifique o token e o chat_id.")
    return {"ok": True}


@router.post("/report-now")
async def report_now(_: dict = Depends(get_current_user)):
    if not telegram_service.is_configured():
        raise HTTPException(status_code=400, detail="Configure o Bot Token e o Chat ID primeiro.")
    await telegram_service._send_status_report()
    return {"ok": True}
