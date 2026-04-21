"""
Telegram notification service.
Sends alerts (send failures, QR disconnect, API errors) and
periodic status reports every 3 hours via Telegram Bot API.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None

# In-memory config — loaded from DB on startup and updated via API
_bot_token: str = ""
_chat_id: str = ""

# Counters for the 3-hour report
_msgs_sent: int = 0
_files_sent: int = 0
_send_errors: int = 0


def configure(bot_token: str, chat_id: str) -> None:
    global _bot_token, _chat_id
    _bot_token = bot_token.strip()
    _chat_id = chat_id.strip()


def is_configured() -> bool:
    return bool(_bot_token and _chat_id)


def record_sent(tipo: str = "text") -> None:
    global _msgs_sent, _files_sent
    if tipo == "file":
        _files_sent += 1
    else:
        _msgs_sent += 1


def record_error() -> None:
    global _send_errors
    _send_errors += 1


async def send(text: str) -> bool:
    if not is_configured():
        return False
    url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
    payload = {"chat_id": _chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                logger.warning("Telegram respondeu %s: %s", r.status_code, r.text[:200])
                return False
        return True
    except Exception as exc:
        logger.error("Erro ao enviar mensagem Telegram: %s", exc)
        return False


async def notify_send_failure(sessao_nome: str, destinatario: str, erro: str) -> None:
    record_error()
    await send(
        f"⚠️ <b>ZapDin — Falha no Envio</b>\n\n"
        f"📱 Sessão: <b>{sessao_nome}</b>\n"
        f"📞 Destinatário: <code>{destinatario}</code>\n"
        f"❌ Erro: {erro}\n"
        f"🕐 {_now()}"
    )


async def notify_disconnected(sessao_nome: str) -> None:
    await send(
        f"🔴 <b>ZapDin — Sessão Desconectada</b>\n\n"
        f"📱 A sessão <b>{sessao_nome}</b> foi desconectada do WhatsApp.\n"
        f"Por favor, acesse o painel e escaneie o QR Code novamente.\n"
        f"🕐 {_now()}"
    )


async def notify_api_error(detail: str) -> None:
    await send(
        f"🚨 <b>ZapDin — Erro na API</b>\n\n"
        f"{detail}\n"
        f"🕐 {_now()}"
    )


async def _send_status_report() -> None:
    from ..services.whatsapp_service import wa_manager

    sessoes = wa_manager.get_status()
    total = len(sessoes)
    conectadas = [s for s in sessoes if s["status"] == "connected"]
    qr_pendente = [s for s in sessoes if s["status"] == "qr"]
    desconectadas = [s for s in sessoes if s["status"] not in ("connected", "qr")]

    linhas_sessoes = ""
    for s in sessoes:
        icon = "✅" if s["status"] == "connected" else ("🟡" if s["status"] == "qr" else "🔴")
        linhas_sessoes += f"  {icon} {s['nome']} — {s['status']}\n"

    texto = (
        f"📊 <b>ZapDin — Relatório de Status</b>\n"
        f"🕐 {_now()}\n\n"
        f"<b>📱 Sessões WhatsApp ({total})</b>\n"
        f"{linhas_sessoes or '  Nenhuma sessão cadastrada\n'}\n"
        f"<b>📤 Envios nas últimas 3h</b>\n"
        f"  ✉️ Mensagens enviadas: {_msgs_sent}\n"
        f"  📎 Arquivos enviados: {_files_sent}\n"
        f"  ❌ Erros de envio: {_send_errors}\n\n"
    )

    if not conectadas:
        texto += "⚠️ <b>Nenhuma sessão conectada!</b> O envio de mensagens está indisponível."
    else:
        texto += f"✅ {len(conectadas)} sessão(ões) ativa(s) e pronta(s) para envio."

    await send(texto)
    _reset_counters()


def _reset_counters() -> None:
    global _msgs_sent, _files_sent, _send_errors
    _msgs_sent = 0
    _files_sent = 0
    _send_errors = 0


def _now() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


async def _loop() -> None:
    INTERVAL = 3 * 60 * 60  # 3 horas
    while True:
        await asyncio.sleep(INTERVAL)
        if is_configured():
            await _send_status_report()


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
