"""
ZapDin — Worker Standalone (ZapDin-Worker.exe)
================================================
Processo independente que gerencia a fila de envios WhatsApp.
Chama a API interna do ZapDin-App (localhost) para despachar mensagens,
mantendo toda a lógica de anti-ban, delays e limites neste processo.

Fluxo:
  1. GET /internal/queue/peek          → próximo item na fila
  2. Verifica horário, limites diários
  3. Aplica spintax na mensagem
  4. Sleep delay aleatório (anti-ban)
  5. GET /internal/sessions/pick       → seleciona sessão WA conectada
  6. POST /internal/queue/dispatch     → app executa o envio real
  7. Volta para 1

Compilado como ZapDin-Worker.exe pelo CI.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger("zapdin.worker")

_BASE_URL  = "http://127.0.0.1:4000"
_POLL_MS   = 0.5   # intervalo de polling quando fila vazia (segundos)
_RETRY_GAP = 5.0   # aguarda N segundos se App não estiver acessível

# ── Config padrão (sobrescrita pela config do App via /api/config endpoint) ───
_DEFAULT_DELAY_MIN   = 1.0
_DEFAULT_DELAY_MAX   = 4.0
_DEFAULT_DAILY_LIMIT = 0     # 0 = sem limite
_DEFAULT_HORA_INICIO = ""
_DEFAULT_HORA_FIM    = ""
_DEFAULT_SPINTAX     = True

# Cache de config (recarrega a cada 60s)
_cfg_cache: dict = {}
_cfg_loaded_at: float = 0.0
_CFG_TTL = 60.0


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg_float(key: str, default: float) -> float:
    try:
        return float(_cfg_cache.get(key, default))
    except (ValueError, TypeError):
        return default


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_cfg_cache.get(key, default))
    except (ValueError, TypeError):
        return default


def _within_hours() -> bool:
    inicio = _cfg_cache.get("wa_hora_inicio", "").strip()
    fim    = _cfg_cache.get("wa_hora_fim", "").strip()
    if not inicio or not fim:
        return True
    now = datetime.now().strftime("%H:%M")
    return inicio <= now <= fim


def process_spintax(text: str) -> str:
    """Expande {opção1|opção2} de dentro para fora."""
    pattern = re.compile(r'\{([^{}]+)\}')
    for _ in range(10):
        new = pattern.sub(lambda m: random.choice(m.group(1).split('|')), text)
        if new == text:
            break
        text = new
    return text


# ─────────────────────────────────────────────────────────────────────────────
#  Carregamento de config via API interna
# ─────────────────────────────────────────────────────────────────────────────

async def _reload_config(client: httpx.AsyncClient) -> None:
    global _cfg_cache, _cfg_loaded_at
    if time.monotonic() - _cfg_loaded_at < _CFG_TTL:
        return
    try:
        r = await client.get(f"{_BASE_URL}/api/config", timeout=5)
        if r.status_code == 200:
            data = r.json()
            _cfg_cache = data if isinstance(data, dict) else {}
            _cfg_loaded_at = time.monotonic()
    except Exception:
        pass  # usa cache anterior


# ─────────────────────────────────────────────────────────────────────────────
#  Loop principal
# ─────────────────────────────────────────────────────────────────────────────

async def run_worker() -> None:
    logger.info("ZapDin Worker iniciado. Conectando em %s…", _BASE_URL)

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                dispatched = await _process_next(client)
            except httpx.ConnectError:
                logger.warning("App não acessível em %s. Aguardando %ss…",
                               _BASE_URL, _RETRY_GAP)
                await asyncio.sleep(_RETRY_GAP)
                continue
            except Exception as exc:
                logger.error("Erro inesperado no worker: %s", exc)
                await asyncio.sleep(_RETRY_GAP)
                continue

            await asyncio.sleep(_POLL_MS if not dispatched else 0)


async def _process_next(client: httpx.AsyncClient) -> bool:
    """Processa o próximo item da fila. Retorna True se despachou algo."""

    await _reload_config(client)

    # ── Verifica janela de horário ────────────────────────────────────────────
    if not _within_hours():
        await asyncio.sleep(30)
        return False

    # ── Peek: próximo item ────────────────────────────────────────────────────
    r = await client.get(f"{_BASE_URL}/internal/queue/peek", timeout=10)
    r.raise_for_status()
    item = r.json()

    if item.get("type") is None:
        return False  # fila vazia

    item_type  = item["type"]   # "text" | "file"
    item_id    = item["id"]
    item_phone = item["phone"]
    content    = item.get("content", "")

    # ── Spintax ───────────────────────────────────────────────────────────────
    spintax_on = _cfg_cache.get("wa_spintax", "1") not in ("0", "false", "")
    processed_content = process_spintax(content) if spintax_on else content

    # ── Delay anti-ban ────────────────────────────────────────────────────────
    delay_min = _cfg_float("wa_delay_min", _DEFAULT_DELAY_MIN)
    delay_max = _cfg_float("wa_delay_max", _DEFAULT_DELAY_MAX)
    delay = random.uniform(delay_min, delay_max)
    logger.info("Queue: %s #%s → aguardando %.1fs (anti-ban)…", item_type, item_id, delay)
    await asyncio.sleep(delay)

    # ── Seleciona sessão ──────────────────────────────────────────────────────
    r = await client.get(f"{_BASE_URL}/internal/sessions/pick", timeout=10)
    r.raise_for_status()
    pick = r.json()

    if not pick.get("available"):
        logger.warning("Nenhuma sessão WA conectada. Aguardando…")
        await asyncio.sleep(10)
        return False

    sessao_id = pick["sessao_id"]

    # ── Verifica limite diário ────────────────────────────────────────────────
    daily_limit = _cfg_int("wa_daily_limit", _DEFAULT_DAILY_LIMIT)
    if daily_limit > 0:
        r = await client.get(
            f"{_BASE_URL}/internal/daily-count/{sessao_id}", timeout=10
        )
        r.raise_for_status()
        today_count = r.json().get("total_today", 0)
        if today_count >= daily_limit:
            logger.info("Sessão %s atingiu limite diário (%d). Aguardando…",
                        sessao_id, daily_limit)
            await asyncio.sleep(60)
            return False

    # ── Despacha via App ──────────────────────────────────────────────────────
    payload = {
        "item_type": item_type,
        "item_id": item_id,
        "sessao_id": sessao_id,
        "processed_content": processed_content,
    }
    r = await client.post(f"{_BASE_URL}/internal/queue/dispatch", json=payload, timeout=60)
    r.raise_for_status()
    result = r.json()

    if result.get("ok"):
        logger.info("Queue: %s #%s → enviado via sessão %s", item_type, item_id, sessao_id)
    else:
        logger.warning("Queue: %s #%s → falhou: %s", item_type, item_id, result.get("error"))

    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Entry-point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # CWD = pasta do executável
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
