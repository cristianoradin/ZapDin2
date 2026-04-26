"""
ZapDin — Serviço de Auto-Atualização (Velopack)
================================================
Verifica atualizações a cada 15 minutos usando o Velopack Update.exe.

Estratégia:
  1. Tenta Update.exe --update <channel_url>  (Velopack instalado)
  2. Fallback informativo: compara versão local com Monitor Central

O Velopack baixa delta packages e aplica no próximo restart.
O NSSM reinicia o serviço automaticamente após a saída do processo.

Variáveis .env relevantes:
  VELOPACK_CHANNEL_URL   URL base dos releases (ex: GitHub Releases latest/download)
  VELOPACK_UPDATE_EXE    Path para o Update.exe (padrão: ./Update.exe)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


# ─────────────────────────────────────────────────────────────────────────────
#  Utilitários
# ─────────────────────────────────────────────────────────────────────────────

def _root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def _current_version() -> str:
    try:
        return json.loads((_root_dir() / "versao.json").read_text())["versao"]
    except Exception:
        return "1.0.0"


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _update_exe_path() -> Path | None:
    configured = settings.velopack_update_exe
    if configured:
        p = Path(configured)
        if not p.is_absolute():
            p = _root_dir() / configured
        if p.exists():
            return p
    p = _root_dir() / "Update.exe"
    return p if p.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
#  Estratégia 1 — Velopack Update.exe
# ─────────────────────────────────────────────────────────────────────────────

async def _velopack_update() -> bool:
    """Chama Update.exe --update <channel_url>. Retorna True se atualizou."""
    update_exe = _update_exe_path()
    if not update_exe:
        return False

    channel_url = settings.velopack_channel_url
    if not channel_url:
        logger.debug("VELOPACK_CHANNEL_URL não configurado — atualização via Velopack desabilitada.")
        return False

    logger.info("Velopack: verificando atualizações em %s…", channel_url)

    try:
        no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = await asyncio.create_subprocess_exec(
            str(update_exe), "--update", channel_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=no_win,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            out = stdout.decode(errors="replace").strip()
            if out:
                logger.info("Velopack output: %s", out[:300])
            logger.info("Velopack: pacote aplicado — será ativado no próximo restart.")
            return True

        err = stderr.decode(errors="replace").strip()
        logger.debug("Velopack: sem atualização (%d): %s", proc.returncode, err[:200])
        return False

    except asyncio.TimeoutError:
        logger.warning("Velopack: timeout ao verificar atualizações.")
        return False
    except Exception as exc:
        logger.error("Velopack: erro: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Estratégia 2 — Fallback: Monitor endpoint (log informativo)
# ─────────────────────────────────────────────────────────────────────────────

async def _monitor_version_check() -> None:
    local = _current_version()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.monitor_url.rstrip('/')}/api/versao/whatsapp")
            if resp.status_code != 200:
                return
            remote: str = resp.json().get("versao", local)
    except Exception as exc:
        logger.debug("Monitor version check falhou: %s", exc)
        return

    if _version_tuple(remote) > _version_tuple(local):
        logger.warning(
            "Nova versão disponível no Monitor: %s → %s. "
            "Configure VELOPACK_CHANNEL_URL para atualização automática.",
            local, remote,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Loop principal
# ─────────────────────────────────────────────────────────────────────────────

async def _loop() -> None:
    await asyncio.sleep(60)  # aguarda boot completo

    while True:
        try:
            updated = await _velopack_update()

            if not updated:
                await _monitor_version_check()

            if updated:
                # Velopack aplicou — reinicia limpo para o NSSM relançar
                logger.info("Reiniciando processo para ativar a atualização…")
                await asyncio.sleep(5)
                os._exit(0)

        except Exception as exc:
            logger.error("Updater erro: %s", exc)

        await asyncio.sleep(900)  # 15 minutos


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())
    logger.info("Updater (Velopack) iniciado.")


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
