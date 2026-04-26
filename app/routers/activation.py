"""
ZapDin — Router de Ativação
============================
GET  /activate           → serve activate.html (kiosk first-run)
POST /api/activate       → valida token no Monitor, grava .env, agenda restart
GET  /api/activate/status → retorna estado atual (locked | active)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.activation import decrypt_config, apply_config_to_env, env_path as get_env_path
from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["activation"])

_static_dir = Path(__file__).parent.parent / "static"


# ─────────────────────────────────────────────────────────────────────────────
#  Modelos
# ─────────────────────────────────────────────────────────────────────────────

class ActivatePayload(BaseModel):
    token: str


# ─────────────────────────────────────────────────────────────────────────────
#  Rotas
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/activate")
async def serve_activation_page():
    """Página de ativação servida no kiosk (primeiro uso)."""
    html = _static_dir / "activate.html"
    if not html.exists():
        return JSONResponse({"error": "Página de ativação não encontrada."}, status_code=500)
    return FileResponse(str(html))


@router.get("/api/activate/status")
async def activation_status():
    """Retorna se o sistema está bloqueado ou ativo."""
    return {"state": settings.app_state}


@router.post("/api/activate")
async def activate(body: ActivatePayload, request: Request):
    """
    Fluxo:
      1. Envia token ao Monitor (POST /api/activate/validate)
      2. Monitor retorna config cifrada com AES-GCM
      3. App descriptografa localmente
      4. Grava .env com APP_STATE=active
      5. Agenda restart do processo (NSSM reinicia o serviço)
    """
    token = body.token.strip()
    if not token:
        return JSONResponse({"ok": False, "error": "Token não pode ser vazio."}, status_code=400)

    # Normaliza formato: remove hífens, maiúsculas (exibido como XXXX-XXXX mas armazenado sem)
    token = token.replace("-", "").upper()

    monitor_url = settings.monitor_url.rstrip("/")

    # ── 1. Valida token no Monitor ────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{monitor_url}/api/activate/validate",
                json={"activation_token": token},
                headers={"x-client-token": settings.monitor_client_token or ""},
            )
    except httpx.ConnectError:
        logger.warning("[activation] Monitor inalcançável em %s", monitor_url)
        return JSONResponse(
            {"ok": False, "error": "Não foi possível conectar ao servidor de ativação. Verifique a rede."},
            status_code=503,
        )
    except Exception as exc:
        logger.error("[activation] Erro ao chamar Monitor: %s", exc)
        return JSONResponse({"ok": False, "error": "Erro interno ao validar token."}, status_code=500)

    if resp.status_code == 401:
        return JSONResponse({"ok": False, "error": "Token inválido ou expirado."}, status_code=401)

    if resp.status_code != 200:
        logger.error("[activation] Monitor respondeu %s: %s", resp.status_code, resp.text[:300])
        return JSONResponse(
            {"ok": False, "error": f"Servidor de ativação retornou erro {resp.status_code}."},
            status_code=502,
        )

    data = resp.json()

    # ── 2. Descriptografa config ──────────────────────────────────────────────
    try:
        config = decrypt_config(
            token=token,
            encrypted_b64=data["encrypted"],
            nonce_b64=data["nonce"],
        )
    except ValueError as exc:
        logger.error("[activation] Falha na descriptografia: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except KeyError:
        logger.error("[activation] Resposta do Monitor sem campos 'encrypted'/'nonce': %s", data)
        return JSONResponse({"ok": False, "error": "Resposta inesperada do servidor."}, status_code=502)

    # ── 3. Grava .env e ativa ──────────────────────────────────────────────────
    try:
        apply_config_to_env(config, get_env_path())
    except Exception as exc:
        logger.error("[activation] Erro ao gravar .env: %s", exc)
        return JSONResponse({"ok": False, "error": "Erro ao salvar configurações."}, status_code=500)

    logger.info("[activation] Ativação bem-sucedida. Reiniciando em 2s…")

    # ── 4. Agenda restart (NSSM vai reiniciar o serviço automaticamente) ──────
    asyncio.create_task(_delayed_restart())

    return JSONResponse({
        "ok": True,
        "message": "Sistema ativado! Reiniciando em instantes…",
    })


async def _delayed_restart() -> None:
    """Aguarda 2s para que o cliente receba a resposta e então encerra o processo.
    Em produção: NSSM detecta a saída e reinicia (AppExit Default Restart).
    Em dev: o .command faz loop e reinicia automaticamente.
    """
    await asyncio.sleep(2)
    logger.info("[activation] Encerrando para reiniciar com nova configuração…")
    sys.exit(0)
