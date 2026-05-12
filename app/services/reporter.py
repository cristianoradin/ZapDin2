"""
Heartbeat service: sends status to the central monitor every 30 seconds.
Envia heartbeat para TODAS as empresas ativas no banco, não apenas a do .env.
"""
import asyncio
import json
import logging
import os

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _read_version() -> str:
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "versao.json")) as f:
            return json.load(f).get("versao", "1.0.0")
    except Exception:
        return "1.0.0"


def _wa_status_for_empresa(empresa_id: int) -> str:
    """Retorna status WA apenas das sessões desta empresa."""
    try:
        from ..core.config import settings as _settings
        if _settings.use_evolution:
            from .evolution_service import evo_manager as wa_manager
        else:
            from .whatsapp_service import wa_manager
        prefix = f"{empresa_id}:"
        statuses = {s.status for k, s in wa_manager._sessions.items() if k.startswith(prefix)}
        logger.debug("[reporter] empresa=%s sessões=%s", empresa_id, statuses or "nenhuma")
        if "connected" in statuses:
            return "connected"
        if "qr_code" in statuses:
            return "qr_code"
        if statuses:
            return "disconnected"
    except Exception as exc:
        logger.warning("[reporter] _wa_status_for_empresa(%s) erro: %s", empresa_id, exc)
    return "disconnected"


async def _send_heartbeat() -> None:
    version = await _read_version()
    monitor_url = settings.monitor_url.rstrip("/")

    # Busca todas as empresas ativas no banco para enviar heartbeat a cada uma
    try:
        empresas = await _get_empresas_ativas()
    except Exception as exc:
        logger.debug("Não foi possível buscar empresas para heartbeat: %s", exc)
        # Fallback: usa token do .env
        empresas = [{"token": settings.monitor_client_token,
                     "nome": settings.client_name,
                     "cnpj": settings.client_cnpj,
                     "id": 0}]

    async with httpx.AsyncClient(timeout=10) as client:
        for emp in empresas:
            token = emp.get("token") or settings.monitor_client_token
            if not token:
                continue
            wa_status = _wa_status_for_empresa(emp.get("id", 0))
            payload = {
                "nome": emp.get("nome", settings.client_name),
                "cnpj": emp.get("cnpj", settings.client_cnpj),
                "versao": version,
                "porta": settings.port,
                "wa_status": wa_status,
            }
            try:
                resp = await client.post(
                    f"{monitor_url}/api/report",
                    json=payload,
                    headers={"x-client-token": token},
                )
                if resp.status_code not in (200, 201):
                    logger.warning("Monitor respondeu %s para empresa %s", resp.status_code, emp.get("nome"))
            except Exception as exc:
                logger.debug("Heartbeat falhou para %s: %s", emp.get("nome"), exc)


async def _get_empresas_ativas() -> list:
    """Lê todas as empresas ativas direto do pool do banco."""
    from ..core.database import _pool  # import tardio para evitar circular
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, nome, cnpj, token FROM empresas WHERE ativo = TRUE AND token IS NOT NULL"
        )
    return [dict(r) for r in rows]


async def _loop() -> None:
    while True:
        await _send_heartbeat()
        await asyncio.sleep(30)


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
