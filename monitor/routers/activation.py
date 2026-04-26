"""
ZapDin Monitor — Endpoint de Ativação de Clientes
===================================================
POST /api/activate/validate
  Recebe um token de ativação de um posto (App).
  Valida o token contra a tabela clientes.
  Criptografa a config daquele cliente com AES-256-GCM (chave derivada do token).
  Retorna o blob cifrado para o App descriptografar localmente.

GET /api/clientes/{id}/activation-token
  Gera (ou regenera) o token de ativação de um cliente.

O token de ativação É DIFERENTE do client_token (heartbeat).
  - client_token: usado para autenticar heartbeats (longo prazo, não expira)
  - activation_token: usado UMA VEZ para ativar o sistema (pode ser rotacionado)
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..core.security import get_current_user

# Importa a função de criptografia (mesma lógica do app)
# Para evitar duplicação de código, o monitor usa o mesmo módulo.
# Em produção, extraia para um pacote compartilhado.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from ..core.config import settings as monitor_settings
try:
    from app.core.activation import encrypt_config
except ImportError:
    # Fallback: implementação inline se o pacote app não estiver disponível
    import base64, json as _json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _SALT = b"zapdin-activation-v1-salt-2024"

    def encrypt_config(token: str, config: dict) -> dict:  # type: ignore
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=_SALT, iterations=200_000)
        key = kdf.derive(token.strip().encode())
        nonce = secrets.token_bytes(12)
        ct = AESGCM(key).encrypt(nonce, _json.dumps(config).encode(), None)
        return {
            "encrypted": base64.b64encode(ct).decode(),
            "nonce": base64.b64encode(nonce).decode(),
        }

logger = logging.getLogger(__name__)
router = APIRouter(tags=["activation-monitor"])


# ─────────────────────────────────────────────────────────────────────────────
#  Modelos
# ─────────────────────────────────────────────────────────────────────────────

class ValidatePayload(BaseModel):
    activation_token: str


# ─────────────────────────────────────────────────────────────────────────────
#  Rotas
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/api/activate/validate")
async def validate_activation_token(
    body: ValidatePayload,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Chamado pelo App ao ativar.
    Valida o token, monta a config do cliente e retorna cifrada.
    NÃO requer autenticação de usuário — autenticado pelo token em si.
    """
    token = body.activation_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token não pode ser vazio.")

    # Normaliza: remove hífens (token exibido como XXXX-XXXX mas armazenado sem)
    token_raw = token.replace("-", "").upper()

    # Busca cliente pelo activation_token
    async with db.execute(
        """SELECT id, nome, cnpj, token AS client_token, erp_token_hint
           FROM clientes
           WHERE activation_token = ? AND ativo = 1""",
        (token_raw,),
    ) as cur:
        row = await cur.fetchone()

    if not row:
        logger.warning("[activation] Token inválido ou cliente inativo: %s…", token[:8])
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    cliente = dict(row)
    logger.info("[activation] Token válido para cliente: %s (id=%s)", cliente["nome"], cliente["id"])

    # Monta config que será entregue ao App após descriptografia
    monitor_base_url = monitor_settings.monitor_public_url
    config_payload = {
        "CLIENT_NAME":          cliente["nome"],
        "CLIENT_CNPJ":          cliente["cnpj"] or "",
        "MONITOR_URL":          monitor_base_url,
        "monitor_client_token": cliente["client_token"],  # token de heartbeat
        "ERP_TOKEN":            cliente.get("erp_token_hint") or secrets.token_urlsafe(24),
    }

    # Criptografa com o token como chave
    encrypted = encrypt_config(token, config_payload)

    # Marca token como usado (opcional: invalidar após uso único)
    # Descomente para uso único:
    # await db.execute("UPDATE clientes SET activation_token=NULL WHERE id=?", (cliente["id"],))
    # await db.commit()

    return {
        "ok": True,
        "encrypted": encrypted["encrypted"],
        "nonce": encrypted["nonce"],
    }


@router.post("/api/clientes/{cliente_id}/activation-token")
async def generate_activation_token(
    cliente_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """
    Gera (ou regenera) o token de ativação para um cliente.
    Formato: XXXX-XXXX-XXXX-XXXX (legível, sem ambiguidade).
    """
    # Verifica se cliente existe
    async with db.execute("SELECT id, nome FROM clientes WHERE id=?", (cliente_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")

    # Gera token legível no formato XXXX-XXXX-XXXX-XXXX
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O/1/I (evita confusão)
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    token = "-".join(raw[i:i+4] for i in range(0, 16, 4))

    await db.execute(
        "UPDATE clientes SET activation_token=? WHERE id=?",
        (token.replace("-", ""), cliente_id),  # armazenado sem hífens
    )
    await db.commit()

    logger.info("[activation] Token gerado para cliente %s (%s): %s",
                row["nome"], cliente_id, token)
    return {"ok": True, "activation_token": token, "cliente_nome": row["nome"]}


@router.get("/api/clientes/{cliente_id}/activation-token")
async def get_activation_token(
    cliente_id: int,
    db: aiosqlite.Connection = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Retorna o token de ativação atual de um cliente (para exibir no painel)."""
    async with db.execute(
        "SELECT activation_token, nome FROM clientes WHERE id=?", (cliente_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")

    raw = row["activation_token"] or ""
    # Formata com hífens para exibição
    if raw and len(raw) == 16:
        token_fmt = "-".join(raw[i:i+4] for i in range(0, 16, 4))
    else:
        token_fmt = raw or "(não gerado)"

    return {"activation_token": token_fmt, "cliente_nome": row["nome"]}
