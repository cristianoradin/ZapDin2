"""
Queue worker — processa mensagens e arquivos enfileirados com delays aleatórios.

Fluxo: ERP grava no banco com status='queued' e retorna imediatamente.
Este worker pega um item por vez, aguarda um delay randômico (anti-ban)
e dispara via WhatsApp. Nunca bloqueia a API.

Multi-tenant: cada item da fila tem empresa_id.
O worker carrega a config da empresa correspondente para aplicar
delays, limites diários, horários e spintax.

Funcionalidades de anti-banimento:
- Delay randômico configurável (wa_delay_min / wa_delay_max)
- Limite diário de mensagens por sessão (wa_daily_limit)
- Restrição de horário de funcionamento (wa_hora_inicio / wa_hora_fim)
- Motor de Spintax: {Olá|Oi|Bom dia} {nome} (wa_spintax=1)
"""
import asyncio
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

UPLOAD_DIR = "data/arquivos"


async def _notify_monitor_numero(phone: str, nome: str, settings) -> None:
    """Notifica o monitor sobre um número contactado. Fire-and-forget."""
    try:
        import httpx
        token = settings.monitor_client_token
        if not token:
            return
        monitor_url = settings.monitor_url.rstrip("/")
        async with httpx.AsyncClient(timeout=4) as client:
            await client.post(
                f"{monitor_url}/api/numeros/registrar",
                json={"phone": phone, "nome": nome},
                headers={"x-client-token": token},
            )
    except Exception as exc:
        logger.debug("_notify_monitor_numero erro: %s", exc)

_task = None

# ── Métricas do worker ────────────────────────────────────────────────────────
_last_processed_at: float = 0.0   # timestamp do último item processado
_processed_count: int = 0          # total de itens processados nesta sessão
_last_error: str = ""              # último erro registrado


def worker_status() -> dict:
    """Retorna métricas do worker para exibição no painel."""
    import time
    running = _task is not None and not _task.done()
    last = _last_processed_at
    ago = int(time.time() - last) if last else None
    return {
        "running": running,
        "processed_count": _processed_count,
        "last_processed_seconds_ago": ago,
        "last_error": _last_error,
    }


# ── Config cache por empresa (recarrega a cada 30s) ───────────────────────────
_cfg_cache: Dict[int, dict] = {}
_cfg_loaded_at: Dict[int, float] = {}
_CFG_TTL = 30.0

_WA_CFG_KEYS = (
    "wa_delay_min", "wa_delay_max",
    "wa_daily_limit",
    "wa_hora_inicio", "wa_hora_fim",
    "wa_spintax",
)


async def _load_cfg(empresa_id: int, get_db_direct) -> dict:
    global _cfg_cache, _cfg_loaded_at
    now = time.monotonic()
    if now - _cfg_loaded_at.get(empresa_id, 0) < _CFG_TTL and empresa_id in _cfg_cache:
        return _cfg_cache[empresa_id]
    try:
        keys_sql = ",".join(f"'{k}'" for k in _WA_CFG_KEYS)
        async with get_db_direct() as db:
            async with db.execute(
                f"SELECT key, value FROM config WHERE empresa_id=? AND key IN ({keys_sql})",
                (empresa_id,),
            ) as cur:
                rows = await cur.fetchall()
        _cfg_cache[empresa_id] = {r["key"]: r["value"] for r in rows}
        _cfg_loaded_at[empresa_id] = now
    except Exception as exc:
        logger.debug("_load_cfg error [empresa %s]: %s", empresa_id, exc)
        _cfg_cache.setdefault(empresa_id, {})
    return _cfg_cache[empresa_id]


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (ValueError, TypeError):
        return default


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (ValueError, TypeError):
        return default


# ── Spintax ───────────────────────────────────────────────────────────────────

def process_spintax(text: str) -> str:
    """Expande {opção1|opção2|opção3} aninhado de dentro para fora."""
    pattern = re.compile(r'\{([^{}]+)\}')
    for _ in range(10):  # proteção contra recursão infinita
        new = pattern.sub(lambda m: random.choice(m.group(1).split('|')), text)
        if new == text:
            break
        text = new
    return text


# ── Business hours ────────────────────────────────────────────────────────────

def _within_hours(cfg: dict) -> bool:
    inicio = cfg.get("wa_hora_inicio", "").strip()
    fim = cfg.get("wa_hora_fim", "").strip()
    if not inicio or not fim:
        return True
    now = datetime.now().strftime("%H:%M")
    return inicio <= now <= fim


# ── Daily limit ───────────────────────────────────────────────────────────────

async def _daily_sent(db, sessao_id: str, empresa_id: int) -> int:
    """Total de mensagens + arquivos enviados hoje por esta sessão/empresa."""
    async with db.execute(
        "SELECT COUNT(*) as cnt FROM mensagens "
        "WHERE sessao_id=? AND empresa_id=? AND status='sent' AND sent_at::date = CURRENT_DATE",
        (sessao_id, empresa_id),
    ) as cur:
        row = await cur.fetchone()
    msg_count = row["cnt"] if row else 0

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM arquivos "
        "WHERE sessao_id=? AND empresa_id=? AND status='sent' AND sent_at::date = CURRENT_DATE",
        (sessao_id, empresa_id),
    ) as cur:
        row = await cur.fetchone()
    arq_count = row["cnt"] if row else 0

    return msg_count + arq_count


# ── Loop principal ────────────────────────────────────────────────────────────

async def _loop() -> None:
    global _last_processed_at, _processed_count, _last_error
    from ..core.config import settings
    from ..core.database import get_db_direct
    if settings.use_evolution:
        from .evolution_service import evo_manager as wa_manager
    else:
        from .whatsapp_service import wa_manager

    logger.info("Queue worker loop iniciado")
    while True:
        try:
            dispatched = await _process_next(wa_manager, settings, get_db_direct)
            if dispatched:
                _last_processed_at = time.time()
                _processed_count += 1
                _last_error = ""
        except Exception as exc:
            _last_error = str(exc)
            logger.error("Queue worker erro: %s", exc, exc_info=True)
            dispatched = False
        await asyncio.sleep(0.2 if dispatched else 1.0)


async def _process_next(wa_manager, settings, get_db_direct) -> bool:
    """Processa o próximo item na fila. Retorna True se processou algo."""
    now_dt = lambda: datetime.now()

    # ── Mensagens de texto ────────────────────────────────────────────────────
    async with get_db_direct() as db:
        async with db.execute(
            "SELECT id, empresa_id, destinatario, nome_destinatario, mensagem FROM mensagens "
            "WHERE status='queued' ORDER BY id LIMIT 1"
        ) as cur:
            msg = await cur.fetchone()

    if msg:
        empresa_id = msg["empresa_id"]
        cfg = await _load_cfg(empresa_id, get_db_direct)

        if not _within_hours(cfg):
            return False

        delay_min = _cfg_float(cfg, "wa_delay_min", settings.dispatch_min_delay)
        delay_max = _cfg_float(cfg, "wa_delay_max", settings.dispatch_max_delay)
        daily_limit = _cfg_int(cfg, "wa_daily_limit", 0)
        spintax_on = cfg.get("wa_spintax", "1") not in ("0", "false", "")

        delay = random.uniform(delay_min, delay_max)
        logger.info("Queue: mensagem %s (empresa %s) → delay %.1fs", msg["id"], empresa_id, delay)
        await asyncio.sleep(delay)

        sessao_id = wa_manager.pick_session(empresa_id)
        if not sessao_id:
            return False

        # Checa limite diário
        if daily_limit > 0:
            async with get_db_direct() as db:
                sent_today = await _daily_sent(db, sessao_id, empresa_id)
            if sent_today >= daily_limit:
                logger.info(
                    "Queue: sessão %s empresa %s atingiu limite diário (%d)",
                    sessao_id, empresa_id, daily_limit,
                )
                return False

        texto = process_spintax(msg["mensagem"]) if spintax_on else msg["mensagem"]

        ok, err = await wa_manager.send_text(sessao_id, empresa_id, msg["destinatario"], texto)
        st = "sent" if ok else "failed"
        async with get_db_direct() as db:
            await db.execute(
                "UPDATE mensagens SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=? AND empresa_id=?",
                (st, sessao_id, now_dt() if ok else None, err, msg["id"], empresa_id),
            )
            await db.commit()
        logger.info("Queue: mensagem %s → %s", msg["id"], st)
        if ok:
            nome = msg["nome_destinatario"] if "nome_destinatario" in msg.keys() else ""
            asyncio.create_task(_notify_monitor_numero(msg["destinatario"], nome or "", settings))
        return True

    # ── Arquivos ──────────────────────────────────────────────────────────────
    async with get_db_direct() as db:
        async with db.execute(
            "SELECT id, empresa_id, destinatario, nome_destinatario, nome_arquivo, nome_original, caption "
            "FROM arquivos WHERE status='queued' ORDER BY id LIMIT 1"
        ) as cur:
            arq = await cur.fetchone()

    if arq:
        empresa_id = arq["empresa_id"]
        cfg = await _load_cfg(empresa_id, get_db_direct)

        if not _within_hours(cfg):
            return False

        delay_min = _cfg_float(cfg, "wa_delay_min", settings.dispatch_min_delay)
        delay_max = _cfg_float(cfg, "wa_delay_max", settings.dispatch_max_delay)
        daily_limit = _cfg_int(cfg, "wa_daily_limit", 0)
        spintax_on = cfg.get("wa_spintax", "1") not in ("0", "false", "")

        delay = random.uniform(delay_min, delay_max)
        logger.info("Queue: arquivo %s (empresa %s) → delay %.1fs", arq["id"], empresa_id, delay)
        await asyncio.sleep(delay)

        sessao_id = wa_manager.pick_session(empresa_id)
        if not sessao_id:
            return False

        # Checa limite diário
        if daily_limit > 0:
            async with get_db_direct() as db:
                sent_today = await _daily_sent(db, sessao_id, empresa_id)
            if sent_today >= daily_limit:
                logger.info(
                    "Queue: sessão %s empresa %s atingiu limite diário (%d)",
                    sessao_id, empresa_id, daily_limit,
                )
                return False

        file_path = os.path.join(UPLOAD_DIR, arq["nome_arquivo"])
        if not os.path.exists(file_path):
            async with get_db_direct() as db:
                await db.execute(
                    "UPDATE arquivos SET status='failed', erro='Arquivo não encontrado no disco' WHERE id=? AND empresa_id=?",
                    (arq["id"], empresa_id),
                )
                await db.commit()
            return True

        caption = process_spintax(arq["caption"] or "") if spintax_on else (arq["caption"] or "")

        ok, err = await wa_manager.send_file(
            sessao_id, empresa_id, arq["destinatario"], file_path,
            arq["nome_original"], caption or None,
        )
        st = "sent" if ok else "failed"
        async with get_db_direct() as db:
            await db.execute(
                "UPDATE arquivos SET status=?, sessao_id=?, sent_at=?, erro=? WHERE id=? AND empresa_id=?",
                (st, sessao_id, now_dt() if ok else None, err, arq["id"], empresa_id),
            )
            await db.commit()
        logger.info("Queue: arquivo %s → %s", arq["id"], st)
        if ok:
            nome = arq["nome_destinatario"] if "nome_destinatario" in arq.keys() else ""
            asyncio.create_task(_notify_monitor_numero(arq["destinatario"], nome or "", settings))
            wa_manager.schedule_status_check(arq["id"], sessao_id, empresa_id, arq["destinatario"])
        return True

    # ── Campanha Envios ───────────────────────────────────────────────────────
    async with get_db_direct() as db:
        async with db.execute(
            "SELECT ce.id, ce.campanha_id, ce.empresa_id, ce.phone, ce.nome, "
            "       c.tipo, c.mensagem "
            "FROM campanha_envios ce "
            "JOIN campanhas c ON c.id = ce.campanha_id "
            "WHERE ce.status='queued' AND c.status='running' "
            "ORDER BY ce.id LIMIT 1"
        ) as cur:
            env = await cur.fetchone()

    if env:
        empresa_id = env["empresa_id"]
        campanha_id = env["campanha_id"]
        cfg = await _load_cfg(empresa_id, get_db_direct)

        if not _within_hours(cfg):
            return False

        delay_min = _cfg_float(cfg, "wa_delay_min", settings.dispatch_min_delay)
        delay_max = _cfg_float(cfg, "wa_delay_max", settings.dispatch_max_delay)
        spintax_on = cfg.get("wa_spintax", "1") not in ("0", "false", "")

        delay = random.uniform(delay_min, delay_max)
        logger.info("Queue: campanha_envio %s campanha %s → delay %.1fs", env["id"], campanha_id, delay)
        await asyncio.sleep(delay)

        sessao_id = wa_manager.pick_session(empresa_id)
        if not sessao_id:
            return False

        tipo = env["tipo"]
        ok = False
        err = None

        if tipo == "text":
            mensagem = env["mensagem"] or ""
            if spintax_on:
                mensagem = process_spintax(mensagem)
            ok, err = await wa_manager.send_text(sessao_id, empresa_id, env["phone"], mensagem)

        elif tipo == "file":
            # Busca arquivos da campanha
            async with get_db_direct() as db2:
                async with db2.execute(
                    "SELECT nome_original, nome_arquivo FROM campanha_arquivos WHERE campanha_id=? ORDER BY id",
                    (campanha_id,),
                ) as cur2:
                    camp_arqs = await cur2.fetchall()

            if not camp_arqs:
                ok, err = False, "Nenhum arquivo na campanha"
            else:
                # Envia todos os arquivos deste contato
                ok = True
                for ca in camp_arqs:
                    file_path = os.path.join(UPLOAD_DIR, ca["nome_arquivo"])
                    if not os.path.exists(file_path):
                        ok, err = False, f"Arquivo {ca['nome_original']} não encontrado"
                        break
                    mensagem = env["mensagem"] or ""
                    if spintax_on:
                        mensagem = process_spintax(mensagem)
                    _ok, _err = await wa_manager.send_file(
                        sessao_id, empresa_id, env["phone"], file_path,
                        ca["nome_original"], mensagem or None,
                    )
                    if not _ok:
                        ok, err = False, _err
                        break
                    await asyncio.sleep(random.uniform(1, 3))

        st = "sent" if ok else "failed"
        async with get_db_direct() as db3:
            await db3.execute(
                "UPDATE campanha_envios SET status=?, sent_at=?, erro=? WHERE id=?",
                (st, now_dt() if ok else None, err, env["id"]),
            )
            if ok:
                await db3.execute(
                    "UPDATE campanhas SET enviados = enviados + 1 WHERE id=?", (campanha_id,)
                )
            else:
                await db3.execute(
                    "UPDATE campanhas SET erros = erros + 1 WHERE id=?", (campanha_id,)
                )
            # Verifica se campanha terminou
            async with db3.execute(
                "SELECT COUNT(*) as cnt FROM campanha_envios WHERE campanha_id=? AND status='queued'",
                (campanha_id,),
            ) as cur3:
                remaining = await cur3.fetchone()
            if remaining and remaining["cnt"] == 0:
                await db3.execute(
                    "UPDATE campanhas SET status='done', done_at=NOW() WHERE id=? AND status='running'",
                    (campanha_id,),
                )
            await db3.commit()

        logger.info("Queue: campanha_envio %s → %s", env["id"], st)
        if ok:
            asyncio.create_task(_notify_monitor_numero(env["phone"], env["nome"] or "", settings))
        return True

    return False


def start() -> None:
    global _task
    _task = asyncio.create_task(_loop())
    logger.info("Queue worker iniciado")


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
