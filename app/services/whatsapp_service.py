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
    'div[contenteditable="true"][data-tab="10"],'
    'div[contenteditable="true"][spellcheck="true"],'
    'div[contenteditable="true"][tabindex="10"],'
    'div[role="textbox"][aria-label="Message"],'
    'div[role="textbox"][aria-label="Mensagem"],'
    'div[role="textbox"][contenteditable="true"]'
)
# Botão "OK" do diálogo de erro "número não está no WhatsApp"
# (ancestral com role="dialog" — não confunde com "Cancelar" do "Iniciando conversa")
_DIALOG_BTN_SEL = '[role="dialog"] button'


async def _safe_click(element) -> None:
    """Clica num elemento. Se for interceptado por overlay, usa JS como fallback."""
    try:
        await element.click(timeout=3000)
    except Exception:
        try:
            await element.evaluate("e => e.click()")
        except Exception:
            pass


class WhatsAppSession:
    def __init__(self, session_id: str, nome: str, empresa_id: int) -> None:
        self.session_id = session_id
        self.nome       = nome
        self.empresa_id = empresa_id
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
                    "--disable-gpu",
                    "--no-first-run",
                    "--mute-audio",
                    "--js-flags=--max-old-space-size=512",
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
                # Adquire o lock antes de navegar — evita race com send_file/send_text
                # Não aguarda se há um envio em andamento (lock ocupado); tenta mais tarde
                try:
                    await asyncio.wait_for(self._lock.acquire(), timeout=5)
                except asyncio.TimeoutError:
                    # Lock ocupado (envio em andamento) — aguarda e tenta novamente
                    await asyncio.sleep(10)
                    continue
                if not self._running:
                    self._lock.release()
                    break
                try:
                    await self._page.goto(
                        "https://web.whatsapp.com",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                finally:
                    self._lock.release()
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
                                # Extrai número do WhatsApp conectado via window.Store
                                try:
                                    raw = await self._page.evaluate(
                                        "() => { try { return window.Store && window.Store.Me ? window.Store.Me.wid._serialized || window.Store.Me.wid.user : null } catch(e) { return null } }"
                                    )
                                    if raw:
                                        # serialized vem como "5511999999999@c.us" → fica só números
                                        self.phone = raw.split("@")[0]
                                    else:
                                        self.phone = None
                                except Exception:
                                    self.phone = None
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
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=90)
        except asyncio.TimeoutError:
            return False, "Sessão ocupada, tente novamente em instantes"
        try:
            try:
                from . import telegram_service
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}&text="
                await asyncio.wait_for(
                    self._page.goto(url, wait_until="domcontentloaded"),
                    timeout=35,
                )

                compose = None
                loop = asyncio.get_event_loop()
                deadline = loop.time() + 60

                while loop.time() < deadline:
                    await asyncio.sleep(1)

                    # Verifica caixa de composição (caminho de sucesso)
                    compose = await self._page.query_selector(_COMPOSE_SEL)
                    if compose:
                        break

                    # Diálogo presente — pode ser:
                    # (a) "Iniciando conversa" → clicar Continuar e aguardar compose
                    # (b) Erro "número não está no WhatsApp" → clicar OK, sem compose
                    all_btns = await self._page.query_selector_all(_DIALOG_BTN_SEL)
                    if all_btns:
                        btn_texts = []
                        for b in all_btns:
                            try:
                                btn_texts.append((await b.inner_text()).strip())
                            except Exception:
                                btn_texts.append("")
                        # Verifica pelo texto do dialog se é erro real de número
                        dialog_text = ""
                        try:
                            dialog_el = await self._page.query_selector('[role="dialog"]')
                            if dialog_el:
                                dialog_text = (await dialog_el.inner_text()).lower()
                        except Exception:
                            pass
                        logger.warning("send_text [%s]: dialog btns=%s texto='%s'",
                                       self.session_id, btn_texts, dialog_text[:80])
                        if "registrado" in dialog_text or "registered" in dialog_text:
                            await _safe_click(all_btns[-1])
                            asyncio.create_task(self._return_home())
                            return False, "Número não registrado no WhatsApp"

                        # Dialog "Iniciar conversa" — tenta confirmar sem clicar Cancelar
                        _cancel_words = {"cancelar", "cancel", "fechar", "close"}
                        confirm_btn = None

                        # 1. Botão <button> que não seja cancelar
                        for b, txt in zip(all_btns, btn_texts):
                            if txt.lower() not in _cancel_words:
                                confirm_btn = b
                                break

                        # 2. Qualquer elemento clicável no dialog com texto "continuar"/"ok"
                        if confirm_btn is None:
                            try:
                                dialog_el = await self._page.query_selector('[role="dialog"]')
                                if dialog_el:
                                    for sel in (
                                        '[data-testid="popup-controls-ok"]',
                                        '[data-testid="confirm-popup-continue"]',
                                        'div[role="button"]',
                                        'span[role="button"]',
                                    ):
                                        cands = await dialog_el.query_selector_all(sel)
                                        for c in cands:
                                            try:
                                                t = (await c.inner_text()).strip().lower()
                                                if t and t not in _cancel_words:
                                                    confirm_btn = c
                                                    break
                                            except Exception:
                                                pass
                                        if confirm_btn:
                                            break
                            except Exception:
                                pass

                        if confirm_btn is not None:
                            logger.warning("send_text [%s]: confirmando dialog '%s'",
                                           self.session_id, (await confirm_btn.inner_text()).strip()[:30])
                            await _safe_click(confirm_btn)
                        else:
                            # Último recurso: pressiona Enter para confirmar o dialog
                            logger.warning("send_text [%s]: sem botão confirmar — pressiona Enter",
                                           self.session_id)
                            await self._page.keyboard.press("Enter")
                            await asyncio.sleep(1)
                        # Aguarda compose (até 15s) — NÃO verifica URL (SPA redireciona pra home)
                        inner_deadline = loop.time() + 15
                        while loop.time() < inner_deadline:
                            await asyncio.sleep(1)
                            compose = await self._page.query_selector(_COMPOSE_SEL)
                            if compose:
                                break
                            # Checa novo dialog de erro
                            err_el = await self._page.query_selector('[role="dialog"]')
                            if err_el:
                                err_txt = (await err_el.inner_text()).lower()
                                if "registrado" in err_txt or "registered" in err_txt:
                                    try:
                                        ok_btn = await self._page.query_selector('[role="dialog"] button')
                                        if ok_btn:
                                            await _safe_click(ok_btn)
                                    except Exception:
                                        pass
                                    asyncio.create_task(self._return_home())
                                    return False, "Número não registrado no WhatsApp"
                        break  # sai do loop externo com compose=None ou compose=encontrado

                if compose is None:
                    try:
                        await self._page.screenshot(path=f"/tmp/wa_debug_{self.session_id}.png")
                        logger.warning("send_text debug screenshot salvo em /tmp/wa_debug_%s.png", self.session_id)
                        # Log do HTML atual para diagnóstico
                        try:
                            title = await self._page.title()
                            url_now = self._page.url
                            logger.warning("send_text página: title=%s url=%s", title, url_now)
                        except Exception:
                            pass
                    except Exception:
                        pass
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
        finally:
            self._lock.release()

    # ── Envio de arquivo ──────────────────────────────────────────────────────
    async def send_file(self, phone: str, file_path: str, caption: str = "") -> Tuple[bool, Optional[str]]:
        if self.status != "connected":
            return False, "Sessão não conectada"
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=90)
        except asyncio.TimeoutError:
            return False, "Sessão ocupada, tente novamente em instantes"
        try:
            try:
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}"
                await asyncio.wait_for(
                    self._page.goto(url, wait_until="domcontentloaded"),
                    timeout=35,
                )
                logger.warning("send_file [%s]: goto OK — url=%s", self.session_id, self._page.url)

                compose = None
                loop = asyncio.get_event_loop()
                deadline = loop.time() + 60

                while loop.time() < deadline:
                    await asyncio.sleep(1)
                    cur_url = self._page.url
                    compose = await self._page.query_selector(_COMPOSE_SEL)
                    if compose:
                        logger.warning("send_file [%s]: compose encontrado em url=%s", self.session_id, cur_url)
                        break
                    all_btns = await self._page.query_selector_all(_DIALOG_BTN_SEL)
                    if all_btns:
                        # Lê o texto de todos os botões para logar e decidir o que clicar
                        btn_texts = []
                        for b in all_btns:
                            try:
                                btn_texts.append((await b.inner_text()).strip())
                            except Exception:
                                btn_texts.append("")

                        # Verifica se é diálogo de erro "não está no WhatsApp"
                        # Nesses dialogs só há 1 botão ("OK") e o texto do dialog menciona "registrado"
                        dialog_text = ""
                        try:
                            dialog_el = await self._page.query_selector('[role="dialog"]')
                            if dialog_el:
                                dialog_text = (await dialog_el.inner_text()).lower()
                        except Exception:
                            pass
                        logger.warning("send_file [%s]: dialog btns=%s texto='%s'",
                                       self.session_id, btn_texts, dialog_text[:80])

                        if "registrado" in dialog_text or "registered" in dialog_text:
                            # Diálogo de erro real — clicar OK e retornar erro
                            await _safe_click(all_btns[-1])
                            asyncio.create_task(self._return_home())
                            return False, "Número não registrado no WhatsApp"

                        # Diálogo "Iniciar conversa" — confirma sem clicar Cancelar
                        _cancel_words = {"cancelar", "cancel", "fechar", "close"}
                        confirm_btn = None
                        for b, txt in zip(all_btns, btn_texts):
                            if txt.lower() not in _cancel_words:
                                confirm_btn = b
                                break
                        if confirm_btn is None:
                            try:
                                dialog_el2 = await self._page.query_selector('[role="dialog"]')
                                if dialog_el2:
                                    for sel in ('div[role="button"]', 'span[role="button"]',
                                                '[data-testid="popup-controls-ok"]'):
                                        cands = await dialog_el2.query_selector_all(sel)
                                        for c in cands:
                                            try:
                                                t = (await c.inner_text()).strip().lower()
                                                if t and t not in _cancel_words:
                                                    confirm_btn = c
                                                    break
                                            except Exception:
                                                pass
                                        if confirm_btn:
                                            break
                            except Exception:
                                pass
                        if confirm_btn is not None:
                            logger.warning("send_file [%s]: confirmando dialog '%s'",
                                           self.session_id, (await confirm_btn.inner_text()).strip()[:30])
                            await _safe_click(confirm_btn)
                        else:
                            logger.warning("send_file [%s]: sem confirmar — pressiona Enter", self.session_id)
                            await self._page.keyboard.press("Enter")
                            await asyncio.sleep(1)
                        # Aguarda compose (até 20s) — após dialog, WA abre a conversa
                        await asyncio.sleep(1.5)  # dá tempo ao WA de abrir a conversa
                        inner_deadline = loop.time() + 20
                        while loop.time() < inner_deadline:
                            await asyncio.sleep(1)
                            compose = await self._page.query_selector(_COMPOSE_SEL)
                            if compose:
                                logger.warning("send_file [%s]: compose após dialog OK url=%s",
                                               self.session_id, self._page.url)
                                await asyncio.sleep(0.5)  # estabiliza antes de anexar
                                break
                            # Checa se apareceu novo dialog de erro
                            err_el = await self._page.query_selector('[role="dialog"]')
                            if err_el:
                                err_txt = (await err_el.inner_text()).lower()
                                if "registrado" in err_txt or "registered" in err_txt:
                                    try:
                                        ok_btn = await self._page.query_selector('[role="dialog"] button')
                                        if ok_btn:
                                            await _safe_click(ok_btn)
                                    except Exception:
                                        pass
                                    asyncio.create_task(self._return_home())
                                    return False, "Número não registrado no WhatsApp"
                        break
                    logger.warning("send_file [%s]: aguardando conversa… url=%s t=%.0fs",
                                self.session_id, cur_url, deadline - loop.time())

                if compose is None:
                    try:
                        await self._page.screenshot(path=f"/tmp/wa_debug_{self.session_id}_file.png")
                        logger.warning("send_file debug screenshot salvo em /tmp/wa_debug_%s_file.png", self.session_id)
                        try:
                            title = await self._page.title()
                            url_now = self._page.url
                            logger.warning("send_file página: title=%s url=%s", title, url_now)
                        except Exception:
                            pass
                    except Exception:
                        pass
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
                logger.warning("send_file [%s]: %s (%s)", self.session_id, _filename, _mime)

                _ATTACH_BTN_SEL = (
                    '[data-testid="plus-rounded"],'        # atual (2025+)
                    '[data-testid="attach-btn"],'          # legado
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
                # Seletor do campo de legenda na tela de preview
                _CAP_SEL = (
                    '[data-testid="media-caption-input-container"] [contenteditable],'
                    '[data-testid="media-caption-input-container"],'
                    '[data-testid="media-caption-input"],'
                    '[data-testid="caption-input"]'
                )
                # Seletor do botão de envio na tela de preview
                _PREV_SEND_SEL = (
                    '[data-testid="wds-ic-send-filled"],'  # atual (2025+)
                    '[data-testid="send"],'
                    '[data-testid="media-send-button"],'
                    '[data-testid="compose-btn-send"]'
                )
                # Seletor alternativo: confirma que preview abriu (container visível)
                _PREV_CONTAINER_SEL = '[data-testid="media-caption-input-container"]'

                is_image_video = _mime.startswith("image/") or _mime.startswith("video/")

                # Abre o menu de anexo para ativar os inputs ocultos
                attach = await self._page.query_selector(_ATTACH_BTN_SEL)
                logger.info("attach button found: %s", attach is not None)
                if attach:
                    await _safe_click(attach)
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
                                        await _safe_click(item)
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
                        logger.warning("set_input_files[accept=%r] fail: %s", acc, _e)

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

                    # Confirma que o preview está aberto (container visível)
                    preview_open = await self._page.query_selector(_PREV_CONTAINER_SEL)
                    if not preview_open:
                        logger.warning("send_file [%s]: aguardando preview… url=%s",
                                    self.session_id, self._page.url[:80])
                        continue

                    # Preenche legenda assim que o campo aparecer
                    if caption and not caption_filled:
                        cap_el = await self._page.query_selector(_CAP_SEL)
                        if cap_el:
                            try:
                                await cap_el.fill(caption)
                                caption_filled = True
                            except Exception:
                                pass

                    # Preview aberto — salva screenshot e dump HTML para diagnóstico
                    try:
                        await self._page.screenshot(path=f"/tmp/wa_debug_{self.session_id}_preview_open.png")
                        # Dump da estrutura HTML ao redor do caption container
                        _html = await self._page.evaluate("""
                            () => {
                                const cap = document.querySelector('[data-testid="media-caption-input-container"]');
                                if (!cap) return 'NO_CAP';
                                // Coleta botões na página toda com seus testids e aria-labels
                                const btns = [...document.querySelectorAll('button')].map(b => ({
                                    testid: b.dataset.testid || '',
                                    aria: b.getAttribute('aria-label') || '',
                                    visible: b.offsetParent !== null,
                                    text: b.innerText?.slice(0,20) || ''
                                }));
                                return JSON.stringify(btns);
                            }
                        """)
                        logger.warning("send_file [%s]: botões na página: %s", self.session_id, _html[:500])
                    except Exception as _de:
                        logger.warning("send_file [%s]: diagnóstico HTML falhou: %s", self.session_id, _de)

                    # ── Estratégia de envio: Enter é mais confiável que JS click ──
                    # JS .click() em componentes React do WhatsApp não dispara
                    # eventos sintéticos corretamente. keyboard.press("Enter")
                    # é um evento real que o WhatsApp sempre aceita no preview.
                    _sent_method = "none"

                    # 1. Tenta clicar o botão de envio do preview via Playwright nativo
                    for _send_sel in (
                        '[data-testid="wds-ic-send-filled"]',
                        '[data-testid="send"]',
                        '[data-testid="media-send-button"]',
                    ):
                        try:
                            _sbtn = await self._page.query_selector(_send_sel)
                            if _sbtn and await _sbtn.is_visible():
                                await _sbtn.click()
                                _sent_method = f"click:{_send_sel}"
                                logger.warning("send_file [%s]: clicou preview send '%s'", self.session_id, _send_sel)
                                break
                        except Exception:
                            pass

                    # 2. Se não encontrou botão específico, usa Enter (sempre funciona)
                    if _sent_method == "none":
                        try:
                            cap_el = await self._page.query_selector(_CAP_SEL)
                            if cap_el:
                                await _safe_click(cap_el)
                                await asyncio.sleep(0.3)
                            await self._page.keyboard.press("Enter")
                            _sent_method = "enter"
                            logger.warning("send_file [%s]: Enter pressionado para envio", self.session_id)
                        except Exception as _ke:
                            logger.warning("send_file [%s]: Enter falhou: %s", self.session_id, _ke)

                    # Aguarda preview fechar — até 15s (conexão lenta pode demorar)
                    _close_dl = loop2.time() + 15
                    _preview_closed = False
                    while loop2.time() < _close_dl:
                        await asyncio.sleep(0.5)
                        if not await self._page.query_selector(_PREV_CONTAINER_SEL):
                            _preview_closed = True
                            break
                    logger.warning("send_file [%s]: preview_closed=%s método=%s",
                                   self.session_id, _preview_closed, _sent_method)

                    if not _preview_closed:
                        # Preview ainda aberto após 15s → tenta Enter uma última vez
                        logger.warning("send_file [%s]: preview persistente, Enter final", self.session_id)
                        try:
                            await self._page.keyboard.press("Enter")
                            await asyncio.sleep(4)
                            _preview_closed = not await self._page.query_selector(_PREV_CONTAINER_SEL)
                            logger.warning("send_file [%s]: após Enter final preview_closed=%s", self.session_id, _preview_closed)
                        except Exception:
                            pass

                    if not _preview_closed:
                        logger.warning("send_file [%s]: arquivo NÃO enviado — preview não fechou", self.session_id)
                        asyncio.create_task(self._return_home())
                        return False, "Arquivo não foi enviado (preview não fechou)"

                    send_btn = True  # preview fechou = arquivo enviado
                    break

                if not send_btn:
                    # Screenshot de diagnóstico para entender o estado da UI
                    try:
                        _scr = f"/tmp/wa_debug_{self.session_id}_preview.png"
                        await self._page.screenshot(path=_scr)
                        logger.warning("send_file [%s]: screenshot de diagnóstico em %s", self.session_id, _scr)
                        _ids = await self._page.evaluate(
                            "() => [...document.querySelectorAll('[data-testid]')].map(e=>e.dataset.testid).filter((v,i,a)=>a.indexOf(v)===i).sort()"
                        )
                        logger.warning("send_file [%s]: data-testids na página: %s", self.session_id, _ids)
                    except Exception as _de:
                        logger.warning("send_file [%s]: diagnóstico falhou: %s", self.session_id, _de)
                    asyncio.create_task(self._return_home())
                    return False, "Preview do arquivo não apareceu"

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
        finally:
            self._lock.release()

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
                    "UPDATE sessoes_wa SET status=?, phone=?, last_seen=NOW() WHERE id=? AND empresa_id=?",
                    (new_status, self.phone, self.session_id, self.empresa_id),
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
        # Chave composta: "{empresa_id}:{session_id}"
        self._sessions: Dict[str, WhatsAppSession] = {}
        self._rr_index: int = 0
        # arquivo_id -> {key, phone, last_status, first_check}
        self._pending_checks: Dict[int, dict] = {}
        self._checker_started: bool = False

    def _key(self, empresa_id: int, session_id: str) -> str:
        return f"{empresa_id}:{session_id}"

    async def load_from_db(self, db) -> None:
        """Carrega todas as sessões de todas as empresas no startup."""
        async with db.execute("SELECT id, nome, empresa_id FROM sessoes_wa") as cur:
            rows = await cur.fetchall()
        for row in rows:
            await self.add_session(row["id"], row["nome"], row["empresa_id"])
        if not self._checker_started:
            self._checker_started = True
            asyncio.create_task(self._status_checker_loop())

    async def add_session(self, session_id: str, nome: str, empresa_id: int) -> None:
        key = self._key(empresa_id, session_id)
        if key in self._sessions:
            return
        sess = WhatsAppSession(session_id, nome, empresa_id)
        self._sessions[key] = sess
        asyncio.create_task(sess.start())

    async def remove_session(self, session_id: str, empresa_id: int) -> None:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.pop(key, None)
        if sess:
            await sess.stop()

    def pick_session(self, empresa_id: int) -> Optional[str]:
        """Retorna o session_id de uma sessão conectada desta empresa (round-robin)."""
        prefix = f"{empresa_id}:"
        connected = [
            k.split(":", 1)[1]
            for k, s in self._sessions.items()
            if k.startswith(prefix) and s.status == "connected"
        ]
        if not connected:
            return None
        idx = self._rr_index % len(connected)
        self._rr_index += 1
        return connected[idx]

    def get_qr(self, session_id: str, empresa_id: int) -> Optional[str]:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.get(key)
        return sess.qr_data if sess else None

    def get_status(self, empresa_id: int) -> list:
        """Retorna status apenas das sessões desta empresa."""
        prefix = f"{empresa_id}:"
        return [
            {"id": k.split(":", 1)[1], "nome": s.nome, "status": s.status, "phone": s.phone}
            for k, s in self._sessions.items()
            if k.startswith(prefix)
        ]

    async def send_text(
        self, session_id: str, empresa_id: int, phone: str, message: str
    ) -> Tuple[bool, Optional[str]]:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.get(key)
        if not sess:
            return False, "Sessão não encontrada"
        return await sess.send_text(phone, message)

    async def send_file(
        self,
        session_id: str,
        empresa_id: int,
        phone: str,
        file_path: str,
        filename: str,
        caption: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.get(key)
        if not sess:
            return False, "Sessão não encontrada"
        return await sess.send_file(phone, file_path, caption or filename)

    def schedule_status_check(
        self, arquivo_id: int, session_id: str, empresa_id: int, phone: str
    ) -> None:
        self._pending_checks[arquivo_id] = {
            "key": self._key(empresa_id, session_id),
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

                sess = self._sessions.get(info["key"])
                if not sess or sess.status != "connected":
                    continue

                new_status = await sess.check_file_status(info["phone"])
                if not new_status:
                    continue

                # Só atualiza se o status avançou
                if _STATUS_ORDER.get(new_status, 0) <= _STATUS_ORDER.get(info["last_status"], 0):
                    continue

                info["last_status"] = new_status
                now = datetime.now()

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
