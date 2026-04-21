import base64
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import aiosqlite

from ..core.database import get_db
from ..core.security import get_current_user
from ..services.whatsapp_service import wa_manager

router = APIRouter(prefix="/api/erp", tags=["erp"])

UPLOAD_DIR = "data/arquivos"


async def _verify_token(x_token: Optional[str], db: aiosqlite.Connection) -> None:
    async with db.execute("SELECT value FROM config WHERE key = 'erp_token'") as cur:
        row = await cur.fetchone()
    stored = row["value"] if row else ""
    if not x_token or x_token != stored:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


class VendaPayload(BaseModel):
    telefone: str
    nome: str
    valor: str
    data: Optional[str] = None
    mensagem_custom: Optional[str] = None


class ArquivoPayload(BaseModel):
    telefone: str
    nome_arquivo: str
    conteudo_base64: str
    mensagem: Optional[str] = None


@router.post("/venda")
async def receber_venda(
    body: VendaPayload,
    x_token: Optional[str] = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _verify_token(x_token, db)

    async with db.execute("SELECT value FROM config WHERE key = 'mensagem_padrao'") as cur:
        row = await cur.fetchone()

    template = row["value"] if row else "Olá {nome}, obrigado pela compra de {valor}!"
    data_str = body.data or datetime.now().strftime("%d/%m/%Y")
    mensagem = body.mensagem_custom or template.replace("{nome}", body.nome).replace("{valor}", body.valor).replace("{data}", data_str)

    sessao_id = wa_manager.pick_session()
    if not sessao_id:
        await db.execute(
            "INSERT INTO mensagens (destinatario, mensagem, status, erro) VALUES (?, ?, 'failed', 'Sem sessão ativa')",
            (body.telefone, mensagem),
        )
        await db.commit()
        raise HTTPException(status_code=503, detail="Nenhuma sessão WhatsApp ativa")

    sucesso, erro = await wa_manager.send_text(sessao_id, body.telefone, mensagem)
    status_msg = "sent" if sucesso else "failed"

    await db.execute(
        "INSERT INTO mensagens (sessao_id, destinatario, mensagem, status, erro, sent_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (sessao_id, body.telefone, mensagem, status_msg, erro),
    )
    await db.commit()

    if not sucesso:
        raise HTTPException(status_code=500, detail=erro)

    return {"ok": True, "sessao": sessao_id}


@router.post("/arquivo")
async def receber_arquivo(
    body: ArquivoPayload,
    x_token: Optional[str] = Header(default=None),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _verify_token(x_token, db)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(body.nome_arquivo)[1] or ".pdf"
    nome_salvo = f"{uuid.uuid4().hex}{ext}"
    caminho = os.path.join(UPLOAD_DIR, nome_salvo)

    conteudo = base64.b64decode(body.conteudo_base64)
    with open(caminho, "wb") as f:
        f.write(conteudo)

    sessao_id = wa_manager.pick_session()
    if not sessao_id:
        await db.execute(
            "INSERT INTO arquivos (nome_original, nome_arquivo, tamanho, destinatario, status) VALUES (?, ?, ?, ?, 'failed')",
            (body.nome_arquivo, nome_salvo, len(conteudo), body.telefone),
        )
        await db.commit()
        raise HTTPException(status_code=503, detail="Nenhuma sessão WhatsApp ativa")

    sucesso, erro = await wa_manager.send_file(sessao_id, body.telefone, caminho, body.nome_arquivo, body.mensagem)
    st = "sent" if sucesso else "failed"

    await db.execute(
        "INSERT INTO arquivos (nome_original, nome_arquivo, tamanho, destinatario, status) VALUES (?, ?, ?, ?, ?)",
        (body.nome_arquivo, nome_salvo, len(conteudo), body.telefone, st),
    )
    await db.commit()

    if not sucesso:
        raise HTTPException(status_code=500, detail=erro)

    return {"ok": True}


@router.get("/config")
async def get_erp_config(
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute("SELECT value FROM config WHERE key = 'erp_token'") as cur:
        row = await cur.fetchone()
    return {"token": row["value"] if row else ""}


@router.post("/config")
async def set_erp_config(
    body: dict,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    token = body.get("token", "")
    await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('erp_token', ?)", (token,))
    await db.commit()
    return {"ok": True}
