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
    '[data-testid="chatlist-header"],'
    'div[aria-label="Lista de conversas"],'
    'div[aria-label="Chat list"],'
    'header[data-testid="chatlist-header"]'
)
_QR_SEL = (
    'div[data-ref] canvas,'
    'canvas[aria-label="Scan me!"],'
    '[data-testid="qrcode"] canvas,'
    '[data-testid="qr-code-container"] canvas,'
    'div[class*="landing-main"] canvas'
)
_COMPOSE_SEL = (
    '[data-testid="conversation-compose-box-input"],'
    'div[aria-label="Message"],'
    'div[aria-label="Mensagem"],'
    'footer [contenteditable="true"],'
    'div[contenteditable="true"][data-tab="10"]'
)
# Botão "OK" do diálogo de erro "número não está no WhatsApp"
# (ancestral com role="dialog" — não confunde com "Cancelar" do "Iniciando conversa")
_DIALOG_BTN_SEL = '[role="dialog"] button'


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
        asyncio.create_task(self._sync_db_status("disconnected"))
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

                    # Pula todas as verificações se um envio está em andamento
                    if self._lock.locked():
                        continue

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
                                self.status = "connected"
                                self.qr_data = None
                                asyncio.create_task(self._sync_db_status("connected"))
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
                                if not qr_b64 or len(qr_b64) < 1000:
                                    # Fallback: screenshot the canvas element
                                    import base64 as _b64
                                    raw = await qr_canvas.screenshot()
                                    qr_b64 = "data:image/png;base64," + _b64.b64encode(raw).decode()
                                if len(qr_b64) > 1000:
                                    self.qr_data = qr_b64
                            except Exception as e:
                                logger.debug("Erro ao capturar QR: %s", e)
                                try:
                                    import base64 as _b64
                                    raw = await qr_canvas.screenshot()
                                    self.qr_data = "data:image/png;base64," + _b64.b64encode(raw).decode()
                                except Exception as e2:
                                    logger.debug("Erro no fallback screenshot QR: %s", e2)
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

            await asyncio.sleep(RECONNECT_WAIT)

            # Tenta recuperar: primeiro a página, se falhar reinicia Playwright completo
            recovered = False
            try:
                if self._browser:
                    pages = self._browser.pages
                    if pages:
                        self._page = pages[0]
                    else:
                        self._page = await self._browser.new_page()
                    await self._page.add_init_script(_WEBDRIVER_SCRIPT)
                    logger.info("Sessão %s — página recriada, reconectando…", self.session_id)
                    recovered = True
            except Exception:
                pass

            if not recovered:
                # Browser crashou (EPIPE, etc.) — reinicia Playwright completo
                logger.warning("Sessão %s — reiniciando Playwright completo…", self.session_id)
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

                from playwright.async_api import async_playwright
                user_data = os.path.join(SESSION_BASE, self.session_id)
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
                    logger.info("Sessão %s — Playwright reiniciado com sucesso", self.session_id)
                except Exception as exc:
                    logger.error("Sessão %s — falha ao reiniciar Playwright: %s", self.session_id, exc)
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

                compose = None
                loop = asyncio.get_event_loop()
                deadline = loop.time() + 40

                while loop.time() < deadline:
                    await asyncio.sleep(1)

                    # Verifica caixa de composição (caminho de sucesso)
                    compose = await self._page.query_selector(_COMPOSE_SEL)
                    if compose:
                        break

                    # Diálogo presente — pode ser:
                    # (a) "Iniciando conversa" → clicar Continuar e aguardar compose
                    # (b) Erro "número não está no WhatsApp" → clicar OK, sem compose
                    btn = await self._page.query_selector(_DIALOG_BTN_SEL)
                    if btn:
                        await btn.click()
                        # Aguarda até 8s para saber se compose aparece
                        inner_deadline = loop.time() + 8
                        while loop.time() < inner_deadline:
                            await asyncio.sleep(1)
                            compose = await self._page.query_selector(_COMPOSE_SEL)
                            if compose:
                                break
                        break  # sai do loop externo com compose=None ou compose=encontrado

                if compose is None:
                    asyncio.create_task(self._return_home())
                    # Verifica se o diálogo ainda está presente (erro real)
                    still_dialog = await self._page.query_selector(_DIALOG_BTN_SEL)
                    if still_dialog:
                        return False, "Número não registrado no WhatsApp"
                    return False, "Tempo esgotado ao abrir conversa"

                await compose.click()
                await self._page.keyboard.type(message)
                await self._page.keyboard.press("Enter")
                await asyncio.sleep(2)
                asyncio.create_task(self._return_home())
                telegram_service.record_sent("text")
                return True, None
            except Exception as exc:
                logger.error("send_text error [%s]: %s", self.session_id, exc)
                asyncio.create_task(self._return_home())
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

                compose = None
                loop = asyncio.get_event_loop()
                deadline = loop.time() + 40

                while loop.time() < deadline:
                    await asyncio.sleep(1)
                    compose = await self._page.query_selector(_COMPOSE_SEL)
                    if compose:
                        break
                    btn = await self._page.query_selector(_DIALOG_BTN_SEL)
                    if btn:
                        await btn.click()
                        inner_deadline = loop.time() + 8
                        while loop.time() < inner_deadline:
                            await asyncio.sleep(1)
                            compose = await self._page.query_selector(_COMPOSE_SEL)
                            if compose:
                                break
                        break

                if compose is None:
                    asyncio.create_task(self._return_home())
                    still_dialog = await self._page.query_selector(_DIALOG_BTN_SEL)
                    if still_dialog:
                        return False, "Número não registrado no WhatsApp"
                    return False, "Tempo esgotado ao abrir conversa"

                # ── Estratégia: abrir menu de anexo → set_input_files no input correto ──
                # Playwright.set_input_files() cria eventos isTrusted=true que o
                # WhatsApp Web (React) aceita, ao contrário de DragEvent injetado via JS.
                import mimetypes as _mt
                _filename = os.path.basename(file_path)
                _mime = _mt.guess_type(file_path)[0] or "application/octet-stream"
                logger.info("send_file [%s]: %s (%s)", self.session_id, _filename, _mime)

                _ATTACH_BTN_SEL = (
                    '[data-testid="attach-btn"],'
                    'span[data-icon="attach-menu-plus"],'
                    '[data-testid="attach-menu-plus"]'
                )
                _SUBMENU_DOC = [
                    '[data-testid="attach-document"]',
                    '[data-testid="mi-attach-document"]',
                    'li[data-testid*="document"]',
                ]
                _SUBMENU_MEDIA = [
                    '[data-testid="attach-media"]',
                    '[data-testid="mi-attach-media"]',
                    'li[data-testid*="media"]',
                    'li[data-testid*="photo"]',
                    'li[data-testid*="image"]',
                ]
                _CAP_SEL = (
                    '[data-testid="media-caption-input"],'
                    '[data-testid="caption-input"]'
                )
                _PREV_SEND_SEL = (
                    '[data-testid="send"],'
                    '[data-testid="media-send-button"],'
                    '[data-testid="compose-btn-send"]'
                )

                is_image_video = _mime.startswith("image/") or _mime.startswith("video/")

                # Abre o menu de anexo para ativar os inputs ocultos
                attach = await self._page.query_selector(_ATTACH_BTN_SEL)
                logger.info("attach button found: %s", attach is not None)
                if attach:
                    await attach.click()
                    await asyncio.sleep(0.8)

                    # Clica no item de submenu adequado ao tipo de arquivo
                    submenu_first  = _SUBMENU_MEDIA if is_image_video else _SUBMENU_DOC
                    submenu_second = _SUBMENU_DOC   if is_image_video else _SUBMENU_MEDIA
                    clicked = False
                    for candidates in [submenu_first, submenu_second]:
                        for sel in candidates:
                            item = await self._page.query_selector(sel)
                            if item:
                                try:
                                    if await item.is_visible():
                                        await item.click()
                                        logger.info("clicked submenu: %s", sel)
                                        await asyncio.sleep(0.5)
                                        clicked = True
                                        break
                                except Exception as _e:
                                    logger.info("submenu click fail %s: %s", sel, _e)
                        if clicked:
                            break
                    if not clicked:
                        logger.warning("nenhum item de submenu encontrado/visível")

                # Coleta todos os inputs disponíveis (com ou sem menu aberto)
                all_inputs = await self._page.query_selector_all('input[type="file"]')
                logger.info("file inputs encontrados: %d", len(all_inputs))
                for i, inp in enumerate(all_inputs):
                    acc = await inp.get_attribute("accept") or ""
                    logger.info("  input[%d] accept=%r", i, acc)

                # Ordena: para documento → sem restrição primeiro;
                #         para mídia    → com restrição primeiro (image/video)
                no_restrict, restricted = [], []
                for inp in all_inputs:
                    acc = (await inp.get_attribute("accept") or "").strip()
                    (restricted if acc and acc != "*" else no_restrict).append(inp)

                ordered = (restricted + no_restrict) if is_image_video else (no_restrict + restricted)
                if not ordered:
                    ordered = list(reversed(all_inputs))

                set_ok = False
                for inp in ordered:
                    acc = await inp.get_attribute("accept") or ""
                    try:
                        await inp.set_input_files(file_path)
                        logger.info("set_input_files OK (accept=%r)", acc)
                        set_ok = True
                        break
                    except Exception as _e:
                        logger.debug("set_input_files[accept=%r] fail: %s", acc, _e)

                if not set_ok:
                    asyncio.create_task(self._return_home())
                    return False, "Nenhum input de arquivo disponível"

                # Aguarda tela de preview aparecer (até 25 s)
                send_btn = None
                loop2 = asyncio.get_event_loop()
                prev_deadline = loop2.time() + 25
                caption_filled = False

                while loop2.time() < prev_deadline:
                    await asyncio.sleep(1)

                    # Preenche legenda assim que o campo aparecer
                    if caption and not caption_filled:
                        cap_el = await self._page.query_selector(_CAP_SEL)
                        if cap_el:
                            try:
                                await cap_el.fill(caption)
                                caption_filled = True
                            except Exception:
                                pass

                    # Confirma que a tela de preview abriu (campo de legenda presente)
                    cap_el = await self._page.query_selector(_CAP_SEL)
                    if cap_el:
                        send_btn = await self._page.query_selector(_PREV_SEND_SEL)
                        if send_btn:
                            logger.info("Preview aberto, botão de envio encontrado")
                            break
                    else:
                        logger.debug(
                            "Aguardando preview… url=%s",
                            self._page.url[:60],
                        )

                if send_btn is None:
                    asyncio.create_task(self._return_home())
                    return False, "Preview do arquivo não apareceu"

                await send_btn.click()
                await asyncio.sleep(3)
                asyncio.create_task(self._return_home())
                from . import telegram_service
                telegram_service.record_sent("file")
                return True, None
            except Exception as exc:
                logger.error("send_file error [%s]: %s", self.session_id, exc)
                asyncio.create_task(self._return_home())
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
                await asyncio.sleep(2)
                await self._page.wait_for_selector(_COMPOSE_SEL, timeout=25_000)
                await asyncio.sleep(2)
                status = await self._page.evaluate(_JS_GET_LAST_STATUS)
                asyncio.create_task(self._return_home())
                return status
            except Exception as exc:
                logger.debug("check_file_status error [%s]: %s", self.session_id, exc)
                return None

    async def _sync_db_status(self, new_status: str) -> None:
        try:
            from ..core.database import get_db_direct
            async with get_db_direct() as db:
                await db.execute(
                    "UPDATE sessoes_wa SET status=?, last_seen=? WHERE id=?",
                    (new_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.session_id),
                )
                await db.commit()
        except Exception as exc:
            logger.debug("_sync_db_status error [%s]: %s", self.session_id, exc)

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
