"""
Queue worker — processa mensagens e arquivos enfileirados com delays aleatórios.

Fluxo: ERP grava no banco com status='queued' e retorna imediatamente.
Este worker pega um item por vez, aguarda um delay randômico (anti-ban)
e dispara via WhatsApp. Nunca bloqueia a API.
"""
import asyncio
import logging
import os
import random
from datetime import datetime

logger = logging.getLogger(__name__)

UPLOAD_DIR = "data/arquivos"

_task = None


async def _loop() -> None:
    from ..core.config import settings
    from ..core.database import get_db_direct
    from .whatsapp_service import wa_manager

    while True:
        try:
            dispatched = await _process_next(wa_manager, settings, get_db_direct)
        except Exception as exc:
            logger.error("Queue worker erro: %s", exc)
            dispatched = False
        # Quando há item, volta logo; quando vazio, dorme 1s antes de checar de novo
        await asyncio.sleep(0.2 if dispatched else 1.0)


async def _process_next(wa_manager, settings, get_db_direct) -> bool:
    """Processa o próximo item na fila. Retorna True se processou algo."""
    now_str = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Mensagens de texto ──────────────────────────────────────────────────────
    async with get_db_direct() as db:
        async with db.execute(
            "SELECT id, destinatario, mensagem FROM mensagens WHERE status='queued' ORDER BY id LIMIT 1"
        ) as cur:
            msg = await cur.fetchone()

    if msg:
        delay = random.uniform(settings.dispatch_min_delay, settings.dispatch_max_delay)
        logger.info("Queue: mensagem %s → delay %.1fs", msg["id"], delay)
        await asyncio.sleep(delay)

        sessao_id = wa_manager.pick_session()
        if not sessao_id:
            return False  # sem sessão ativa, tenta mais tarde

        ok, err = await wa_manager.send_text(sessao_id, msg["destinatario"], msg["mensagem"])
        st = "sent" if ok else "failed"
        async with get_db_direct() as db:
            await db.execute(
                "UPDATE mensagens SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=?",
                (st, sessao_id, now_str() if ok else None, err, msg["id"]),
            )
            await db.commit()
        logger.info("Queue: mensagem %s → %s", msg["id"], st)
        return True

    # ── Arquivos ────────────────────────────────────────────────────────────────
    async with get_db_direct() as db:
        async with db.execute(
            "SELECT id, destinatario, nome_arquivo, nome_original, caption FROM arquivos WHERE status='queued' ORDER BY id LIMIT 1"
        ) as cur:
            arq = await cur.fetchone()

    if arq:
        delay = random.uniform(settings.dispatch_min_delay, settings.dispatch_max_delay)
        logger.info("Queue: arquivo %s → delay %.1fs", arq["id"], delay)
        await asyncio.sleep(delay)

        sessao_id = wa_manager.pick_session()
        if not sessao_id:
            return False

        file_path = os.path.join(UPLOAD_DIR, arq["nome_arquivo"])
        if not os.path.exists(file_path):
            async with get_db_direct() as db:
                await db.execute(
                    "UPDATE arquivos SET status='failed', erro='Arquivo não encontrado no disco' WHERE id=?",
                    (arq["id"],),
                )
                await db.commit()
            return True

        ok, err = await wa_manager.send_file(
            sessao_id, arq["destinatario"], file_path,
            arq["nome_original"], arq["caption"],
        )
        st = "sent" if ok else "failed"
        async with get_db_direct() as db:
            await db.execute(
                "UPDATE arquivos SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=?",
                (st, sessao_id, now_str() if ok else None, err, arq["id"]),
            )
            await db.commit()
        logger.info("Queue: arquivo %s → %s", arq["id"], st)

        if ok:
            wa_manager.schedule_status_check(arq["id"], sessao_id, arq["destinatario"])
        return True

    return False


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())
    logger.info("Queue worker iniciado (delay %.1f–%.1fs)")


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
