"""
Auto-update service: checks the monitor for a newer version every 15 minutes.
If a new version is found, performs git pull and restarts the process.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def _current_version() -> str:
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "versao.json")) as f:
            return json.load(f).get("versao", "1.0.0")
    except Exception:
        return "1.0.0"


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


async def _check_and_update() -> None:
    local = await _current_version()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.monitor_url}/api/versao/whatsapp")
            if resp.status_code != 200:
                return
            remote_version: str = resp.json().get("versao", local)
    except Exception as exc:
        logger.debug("Update check falhou: %s", exc)
        return

    if _version_tuple(remote_version) <= _version_tuple(local):
        return

    logger.info("Nova versão disponível: %s → %s. Atualizando…", local, remote_version)
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        subprocess.run(["git", "-C", repo_dir, "pull", "--ff-only"], check=True, timeout=60)
        logger.info("git pull concluído. Reiniciando processo…")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as exc:
        logger.error("Falha ao atualizar: %s", exc)


async def _loop() -> None:
    # Wait a bit before first check so the app can fully boot
    await asyncio.sleep(60)
    while True:
        await _check_and_update()
        await asyncio.sleep(900)  # 15 minutes


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
