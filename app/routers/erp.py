import base64
import os
import secrets
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

router = APIRouter(prefix="/api/erp", tags=["erp"])

UPLOAD_DIR = "data/arquivos"

# In-memory: last ERP connection info
_last_call: dict = {
    "timestamp": None,
    "ip": None,
    "endpoint": None,
    "status": None,   # "ok" | "error"
    "total_calls": 0,
}


def _record_call(request: Request, endpoint: str, ok: bool) -> None:
    _last_call["timestamp"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _last_call["ip"] = request.client.host if request.client else "?"
    _last_call["endpoint"] = endpoint
    _last_call["status"] = "ok" if ok else "error"
    _last_call["total_calls"] += 1


async def _verify_token(x_token: Optional[str], db) -> None:
    async with db.execute("SELECT value FROM config WHERE key = 'erp_token'") as cur:
        row = await cur.fetchone()
    stored = row["value"] if row else ""
    if not stored:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ERP não configurado")
    if not x_token or x_token != stored:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


class Produto(BaseModel):
    nome: str
    quantidade: Optional[float] = 1
    valor_unitario: Optional[str] = ""


class VendaPayload(BaseModel):
    telefone: str
    nome: str
    # Campos financeiros
    valor_total_itens: Optional[str] = ""
    valor_total: Optional[str] = ""
    # Compatibilidade legada
    valor: Optional[str] = ""
    data: Optional[str] = None
    # Lista de produtos (opcional)
    produtos: Optional[List[Produto]] = None
    mensagem_custom: Optional[str] = None


class ArquivoPayload(BaseModel):
    telefone: str
    nome_arquivo: str
    conteudo_base64: str
    mensagem: Optional[str] = None


def _montar_lista_produtos(produtos: List[Produto]) -> str:
    linhas = []
    for p in produtos:
        qtd = int(p.quantidade) if p.quantidade == int(p.quantidade) else p.quantidade
        linha = f"• {p.nome} (x{qtd})"
        if p.valor_unitario:
            linha += f" — R$ {p.valor_unitario}"
        linhas.append(linha)
    return "\n".join(linhas)


def _normalizar_telefone(telefone: str) -> str:
    """Garante DDI 55 — ERP envia apenas DDD+número."""
    digits = "".join(c for c in telefone if c.isdigit())
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _aplicar_template(template: str, body: VendaPayload, telefone_normalizado: str) -> str:
    data_str = body.data or datetime.now().strftime("%d/%m/%Y")
    valor_exibir = body.valor_total or body.valor or ""
    produtos_str = _montar_lista_produtos(body.produtos) if body.produtos else ""

    return (
        template
        .replace("{nome}", body.nome)
        .replace("{telefone}", telefone_normalizado)
        .replace("{valor}", valor_exibir)
        .replace("{valor_total}", body.valor_total or body.valor or "")
        .replace("{valor_total_itens}", body.valor_total_itens or "")
        .replace("{data}", data_str)
        .replace("{produtos}", produtos_str)
    )


@router.post("/venda")
async def receber_venda(
    body: VendaPayload,
    request: Request,
    x_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    await _verify_token(x_token, db)

    telefone = _normalizar_telefone(body.telefone)

    async with db.execute("SELECT value FROM config WHERE key = 'mensagem_padrao'") as cur:
        row = await cur.fetchone()

    template = row["value"] if row else "Olá {nome}, obrigado pela sua compra de {valor_total} em {data}!"
    mensagem = body.mensagem_custom or _aplicar_template(template, body, telefone)

    # Enfileira para disparo assíncrono — API retorna imediatamente
    await db.execute(
        "INSERT INTO mensagens (destinatario, mensagem, tipo, status) VALUES (?, ?, 'text', 'queued')",
        (telefone, mensagem),
    )
    await db.commit()
    _record_call(request, "/api/erp/venda", True)
    return {"ok": True, "queued": True}


@router.post("/arquivo")
async def receber_arquivo(
    body: ArquivoPayload,
    request: Request,
    x_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    await _verify_token(x_token, db)

    telefone = _normalizar_telefone(body.telefone)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(body.nome_arquivo)[1] or ".pdf"
    nome_salvo = f"{uuid.uuid4().hex}{ext}"
    caminho = os.path.join(UPLOAD_DIR, nome_salvo)

    conteudo = base64.b64decode(body.conteudo_base64)
    with open(caminho, "wb") as f:
        f.write(conteudo)

    # Enfileira para disparo assíncrono — API retorna imediatamente
    await db.execute(
        "INSERT INTO arquivos (nome_original, nome_arquivo, tamanho, destinatario, status, caption) VALUES (?, ?, ?, ?, 'queued', ?)",
        (body.nome_arquivo, nome_salvo, len(conteudo), telefone, body.mensagem),
    )
    await db.commit()
    _record_call(request, "/api/erp/arquivo", True)
    return {"ok": True, "queued": True}


@router.get("/status")
async def erp_status(_: dict = Depends(get_current_user)):
    return _last_call


@router.get("/config")
async def get_erp_config(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    async with db.execute("SELECT value FROM config WHERE key = 'erp_token'") as cur:
        row = await cur.fetchone()
    return {"token": row["value"] if row else ""}


@router.post("/config")
async def set_erp_config(
    body: dict,
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    token = body.get("token", "")
    await db.execute("INSERT INTO config (key, value) VALUES ('erp_token', ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (token,))
    await db.commit()
    return {"ok": True}


@router.post("/gerar-token")
async def gerar_token(
    db=Depends(get_db),
    _: dict = Depends(get_current_user),
):
    novo_token = secrets.token_urlsafe(32)
    await db.execute("INSERT INTO config (key, value) VALUES ('erp_token', ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (novo_token,))
    await db.commit()
    return {"ok": True, "token": novo_token}
