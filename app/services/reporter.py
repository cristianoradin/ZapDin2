"""
Heartbeat service: sends status to the central monitor every 30 seconds.
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


async def _send_heartbeat() -> None:
    version = await _read_version()
    payload = {
        "nome": settings.client_name,
        "cnpj": settings.client_cnpj,
        "versao": version,
        "porta": settings.port,
    }
    headers = {"x-client-token": settings.monitor_client_token}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{settings.monitor_url}/api/report", json=payload, headers=headers)
            if resp.status_code not in (200, 201):
                logger.warning("Monitor respondeu %s", resp.status_code)
    except Exception as exc:
        logger.debug("Heartbeat falhou: %s", exc)


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
