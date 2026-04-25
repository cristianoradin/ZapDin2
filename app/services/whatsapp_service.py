"""
WhatsApp automation via Playwright + WhatsApp Web.
- Sessões persistidas em disco: reconecta sem novo QR após reinício
- Monitor com reconexão automática em loop externo (não para nunca)
- Detecção de travamento: recarrega página se ficar >90s sem progresso
- Recuperação de crash: recria página Playwright sem reiniciar o browser
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_JS_GET_LAST_STATUS = """
() => {
    const icons = Array.from(document.querySelectorAll(
        '[data-testid="msg-check"], [data-testid="msg-dblcheck"]'
    ));
    if (!icons.length) return null;
    const last = icons[icons.length - 1];
    if (last.dataset.testid === 'msg-dblcheck') {
        const paths = Array.from(last.querySelectorAll('path'));
        const isBlue = paths.some(p => {
            const fill = (p.getAttribute('fill') || '').toLowerCase();
            return fill === '#53bdeb' || fill.includes('53bdeb');
        });
        return isBlue ? 'read' : 'delivered';
    }
    return 'sent';
}
"""

SESSION_BASE = "data/wa_sessions"
_WEBDRIVER_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_LOGGED_IN_SEL = (
    '[data-testid="default-user"],'
    '[data-testid="chat-list-title"],'
    '#side,'
    'div[aria-label="Lista de conversas"]'
)
_QR_SEL = 'div[data-ref] canvas, canvas[aria-label="Scan me!"]'


class WhatsAppSession:
    def __init__(self, session_id: str, nome: str) -> None:
        self.session_id = session_id
        self.nome       = nome
        self.status: str        = "disconnected"
        self.qr_data: Optional[str] = None
        self.phone: Optional[str]   = None
        self._pw      = None
        self._browser = None
        self._page    = None
        self._lock    = asyncio.Lock()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        from playwright.async_api import async_playwright

        user_data = os.path.join(SESSION_BASE, self.session_id)
        os.makedirs(user_data, exist_ok=True)

        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch_persistent_context(
                user_data_dir=user_data,
                headless=True,
                user_agent=_UA,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            self._page = (
                self._browser.pages[0]
                if self._browser.pages
                else await self._browser.new_page()
            )
            await self._page.add_init_script(_WEBDRIVER_SCRIPT)
            self.status = "connecting"
            asyncio.create_task(self._monitor_loop())
        except Exception as exc:
            logger.error("Sessão %s falhou ao iniciar: %s", self.session_id, exc)
            self.status = "error"
            self._running = False

    async def stop(self) -> None:
        self._running = False
        self.status = "disconnected"
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    # ── Monitor principal — loop externo nunca para ───────────────────────────
    async def _monitor_loop(self) -> None:
        from . import telegram_service
        STUCK_TIMEOUT = 90   # segundos sem progresso → recarrega página
        RECONNECT_WAIT = 10  # segundos antes de tentar reconectar após erro

        while self._running:
            stuck_since: Optional[datetime] = datetime.now()
            try:
                await self._page.goto(
                    "https://web.whatsapp.com",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                stuck_since = datetime.now()

                while self._running:
                    await asyncio.sleep(3)

                    # Verifica se a página ainda está viva
                    try:
                        await self._page.evaluate("1")
                    except Exception:
                        logger.warning("Sessão %s — página morta, recriando…", self.session_id)
                        break  # sai do loop interno → reconecta

                    try:
                        # ── Conectado ──────────────────────────────────────
                        logged_in = await self._page.query_selector(_LOGGED_IN_SEL)
                        if logged_in:
                            if self.status != "connected":
                                logger.info("Sessão %s conectada", self.session_id)
                                stuck_since = None
                            self.status   = "connected"
                            self.qr_data  = None
                            await asyncio.sleep(15)
                            continue

                        # ── QR Code ────────────────────────────────────────
                        qr_canvas = await self._page.query_selector(_QR_SEL)
                        if qr_canvas:
                            if self.status == "connected":
                                asyncio.create_task(
                                    telegram_service.notify_disconnected(self.nome)
                                )
                            self.status = "qr"
                            stuck_since = datetime.now()
                            try:
                                qr_b64 = await self._page.evaluate(
                                    "(canvas) => canvas.toDataURL('image/png')", qr_canvas
                                )
                                if len(qr_b64) > 1000:
                                    self.qr_data = qr_b64
                            except Exception as e:
                                logger.debug("Erro ao capturar QR: %s", e)
                            continue

                        # ── Conectando (carregando) ────────────────────────
                        self.status = "connecting"

                        # Detecção de travamento
                        if stuck_since and (datetime.now() - stuck_since).seconds > STUCK_TIMEOUT:
                            logger.warning(
                                "Sessão %s travada há %ds — recarregando…",
                                self.session_id, STUCK_TIMEOUT,
                            )
                            stuck_since = datetime.now()
                            try:
                                await self._page.reload(
                                    wait_until="domcontentloaded", timeout=30_000
                                )
                            except Exception:
                                break  # sai para reconectar

                    except Exception as inner:
                        logger.debug("Monitor inner [%s]: %s", self.session_id, inner)

            except asyncio.CancelledError:
                return

            except Exception as exc:
                logger.error("Sessão %s — erro no loop: %s", self.session_id, exc)
                self.status = "connecting"
                asyncio.create_task(
                    telegram_service.notify_api_error(
                        f"Sessão <b>{self.nome}</b> — erro: {exc}. Reconectando…"
                    )
                )

            if not self._running:
                break

            # Tenta recuperar a página antes de aguardar
            await asyncio.sleep(RECONNECT_WAIT)
            try:
                if self._browser:
                    pages = self._browser.pages
                    if pages:
                        self._page = pages[0]
                    else:
                        self._page = await self._browser.new_page()
                    await self._page.add_init_script(_WEBDRIVER_SCRIPT)
                    logger.info("Sessão %s — página recriada, reconectando…", self.session_id)
            except Exception as exc:
                logger.error("Sessão %s — não conseguiu recriar página: %s", self.session_id, exc)
                self.status = "error"
                return

        logger.info("Sessão %s — monitor encerrado", self.session_id)

    # ── Envio de texto ────────────────────────────────────────────────────────
    async def send_text(self, phone: str, message: str) -> Tuple[bool, Optional[str]]:
        if self.status != "connected":
            return False, "Sessão não conectada"
        async with self._lock:
            try:
                from . import telegram_service
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}&text="
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._page.wait_for_selector(
                    '[data-testid="conversation-compose-box-input"]', timeout=20_000
                )
                await self._page.fill(
                    '[data-testid="conversation-compose-box-input"]', message
                )
                await self._page.keyboard.press("Enter")
                await asyncio.sleep(2)
                # Volta para a página principal para não ficar em URL de envio
                asyncio.create_task(self._return_home())
                telegram_service.record_sent("text")
                return True, None
            except Exception as exc:
                logger.error("send_text error [%s]: %s", self.session_id, exc)
                from . import telegram_service
                asyncio.create_task(
                    telegram_service.notify_send_failure(self.nome, phone, str(exc))
                )
                return False, str(exc)

    # ── Envio de arquivo ──────────────────────────────────────────────────────
    async def send_file(self, phone: str, file_path: str, caption: str = "") -> Tuple[bool, Optional[str]]:
        if self.status != "connected":
            return False, "Sessão não conectada"
        async with self._lock:
            try:
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}"
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._page.wait_for_selector(
                    '[data-testid="conversation-compose-box-input"]', timeout=20_000
                )

                attach = await self._page.query_selector('[data-testid="attach-menu-plus"]')
                if attach:
                    await attach.click()
                    await asyncio.sleep(0.5)

                file_input = await self._page.query_selector('input[type="file"]')
                if file_input:
                    await file_input.set_input_files(file_path)
                    await asyncio.sleep(1)
                    if caption:
                        cap_input = await self._page.query_selector(
                            '[data-testid="media-caption-input"]'
                        )
                        if cap_input:
                            await cap_input.fill(caption)
                    send_btn = await self._page.query_selector('[data-testid="send"]')
                    if send_btn:
                        await send_btn.click()
                    await asyncio.sleep(3)
                    asyncio.create_task(self._return_home())
                    from . import telegram_service
                    telegram_service.record_sent("file")
                    return True, None

                return False, "Input de arquivo não encontrado"
            except Exception as exc:
                logger.error("send_file error [%s]: %s", self.session_id, exc)
                from . import telegram_service
                asyncio.create_task(
                    telegram_service.notify_send_failure(self.nome, phone, str(exc))
                )
                return False, str(exc)

    async def check_file_status(self, phone: str) -> Optional[str]:
        """Abre a conversa e lê o status da última mensagem enviada."""
        if self.status != "connected":
            return None
        async with self._lock:
            try:
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}"
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._page.wait_for_selector(
                    '[data-testid="conversation-compose-box-input"]', timeout=20_000
                )
                await asyncio.sleep(2)
                status = await self._page.evaluate(_JS_GET_LAST_STATUS)
                asyncio.create_task(self._return_home())
                return status
            except Exception as exc:
                logger.debug("check_file_status error [%s]: %s", self.session_id, exc)
                return None

    async def _return_home(self) -> None:
        """Volta para a página principal do WhatsApp Web após envio."""
        await asyncio.sleep(1)
        try:
            await self._page.goto(
                "https://web.whatsapp.com",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
        except Exception:
            pass


# ── Manager ───────────────────────────────────────────────────────────────────

_STATUS_ORDER = {"sent": 1, "delivered": 2, "read": 3}


class WhatsAppManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, WhatsAppSession] = {}
        self._rr_index: int = 0
        # arquivo_id -> {session_id, phone, last_status, first_check}
        self._pending_checks: Dict[int, dict] = {}
        self._checker_started: bool = False

    async def load_from_db(self, db) -> None:
        async with db.execute("SELECT id, nome FROM sessoes_wa") as cur:
            rows = await cur.fetchall()
        for row in rows:
            await self.add_session(row["id"], row["nome"])
        if not self._checker_started:
            self._checker_started = True
            asyncio.create_task(self._status_checker_loop())

    async def add_session(self, session_id: str, nome: str) -> None:
        if session_id in self._sessions:
            return
        sess = WhatsAppSession(session_id, nome)
        self._sessions[session_id] = sess
        asyncio.create_task(sess.start())

    async def remove_session(self, session_id: str) -> None:
        sess = self._sessions.pop(session_id, None)
        if sess:
            await sess.stop()

    def pick_session(self) -> Optional[str]:
        connected = [sid for sid, s in self._sessions.items() if s.status == "connected"]
        if not connected:
            return None
        idx = self._rr_index % len(connected)
        self._rr_index += 1
        return connected[idx]

    def get_qr(self, session_id: str) -> Optional[str]:
        sess = self._sessions.get(session_id)
        return sess.qr_data if sess else None

    def get_status(self) -> list:
        return [
            {"id": sid, "nome": s.nome, "status": s.status, "phone": s.phone}
            for sid, s in self._sessions.items()
        ]

    async def send_text(self, session_id: str, phone: str, message: str) -> Tuple[bool, Optional[str]]:
        sess = self._sessions.get(session_id)
        if not sess:
            return False, "Sessão não encontrada"
        return await sess.send_text(phone, message)

    async def send_file(
        self, session_id: str, phone: str, file_path: str, filename: str, caption: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        sess = self._sessions.get(session_id)
        if not sess:
            return False, "Sessão não encontrada"
        return await sess.send_file(phone, file_path, caption or filename)

    def schedule_status_check(self, arquivo_id: int, session_id: str, phone: str) -> None:
        self._pending_checks[arquivo_id] = {
            "session_id": session_id,
            "phone": phone,
            "last_status": "sent",
            "first_check": time.time(),
        }

    async def _status_checker_loop(self) -> None:
        from ..core.database import get_db_direct
        CHECK_INTERVAL = 30   # segundos entre rodadas
        MAX_AGE = 86_400      # 24h — para de checar depois disso

        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            if not self._pending_checks:
                continue

            ids_to_remove: List[int] = []
            for arquivo_id, info in list(self._pending_checks.items()):
                # Remove entradas muito antigas
                if time.time() - info["first_check"] > MAX_AGE:
                    ids_to_remove.append(arquivo_id)
                    continue

                sess = self._sessions.get(info["session_id"])
                if not sess or sess.status != "connected":
                    continue

                new_status = await sess.check_file_status(info["phone"])
                if not new_status:
                    continue

                # Só atualiza se o status avançou
                if _STATUS_ORDER.get(new_status, 0) <= _STATUS_ORDER.get(info["last_status"], 0):
                    continue

                info["last_status"] = new_status
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                try:
                    async with get_db_direct() as db:
                        if new_status == "delivered":
                            await db.execute(
                                "UPDATE arquivos SET status='delivered', delivered_at=? WHERE id=?",
                                (now, arquivo_id),
                            )
                        elif new_status == "read":
                            await db.execute(
                                "UPDATE arquivos SET status='read', read_at=? WHERE id=?",
                                (now, arquivo_id),
                            )
                        await db.commit()
                    logger.info("Arquivo %s status atualizado para %s", arquivo_id, new_status)
                except Exception as exc:
                    logger.error("Erro ao atualizar status do arquivo %s: %s", arquivo_id, exc)

                if new_status == "read":
                    ids_to_remove.append(arquivo_id)

            for aid in ids_to_remove:
                self._pending_checks.pop(aid, None)


wa_manager = WhatsAppManager()
