"""
WhatsApp automation via Playwright + WhatsApp Web.
Manages multiple browser sessions with round-robin dispatch.
"""
import asyncio
import base64
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# User data dir per session
SESSION_BASE = "data/wa_sessions"


class WhatsAppSession:
    def __init__(self, session_id: str, nome: str) -> None:
        self.session_id = session_id
        self.nome = nome
        self.status: str = "disconnected"
        self.qr_data: Optional[str] = None
        self.phone: Optional[str] = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        user_data = os.path.join(SESSION_BASE, self.session_id)
        os.makedirs(user_data, exist_ok=True)

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
        self.status = "connecting"
        asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self.status = "disconnected"
        try:
            if self._browser:
                await self._browser.close()
            if hasattr(self, "_pw") and self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def _monitor_loop(self) -> None:
        """Navigate to WhatsApp Web and watch for QR / login state."""
        try:
            await self._page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60_000)
            while True:
                await asyncio.sleep(3)
                try:
                    # Check if already logged in
                    logged_in = await self._page.query_selector('[data-testid="default-user"]')
                    if logged_in:
                        self.status = "connected"
                        self.qr_data = None
                        # Try to get phone number from profile
                        try:
                            title = await self._page.title()
                            if "WhatsApp" in title:
                                pass
                        except Exception:
                            pass
                        await asyncio.sleep(30)
                        continue

                    # Check for QR canvas
                    qr_canvas = await self._page.query_selector('canvas[aria-label="Scan me!"]')
                    if qr_canvas:
                        self.status = "qr"
                        try:
                            qr_b64 = await self._page.evaluate(
                                "(canvas) => canvas.toDataURL('image/png')", qr_canvas
                            )
                            self.qr_data = qr_b64
                        except Exception:
                            pass
                        continue

                    # Check for loading / pairing
                    loading = await self._page.query_selector('[data-testid="intro-md-beta-logo-dark"]')
                    if loading:
                        self.status = "connecting"
                        continue

                except Exception as inner:
                    logger.debug("Monitor inner error: %s", inner)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Session %s monitor crashed: %s", self.session_id, exc)
            self.status = "error"

    async def send_text(self, phone: str, message: str) -> Tuple[bool, Optional[str]]:
        if self.status != "connected":
            return False, "Sessão não conectada"
        async with self._lock:
            try:
                # Format number: remove non-digits, add @c.us suffix not needed for URL
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}&text="
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._page.wait_for_selector('[data-testid="conversation-compose-box-input"]', timeout=20_000)
                await self._page.fill('[data-testid="conversation-compose-box-input"]', message)
                await self._page.keyboard.press("Enter")
                await asyncio.sleep(2)
                return True, None
            except Exception as exc:
                logger.error("send_text error: %s", exc)
                return False, str(exc)

    async def send_file(self, phone: str, file_path: str, caption: str = "") -> Tuple[bool, Optional[str]]:
        if self.status != "connected":
            return False, "Sessão não conectada"
        async with self._lock:
            try:
                number = "".join(c for c in phone if c.isdigit())
                url = f"https://web.whatsapp.com/send?phone={number}"
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._page.wait_for_selector('[data-testid="conversation-compose-box-input"]', timeout=20_000)

                # Click attach button
                attach = await self._page.query_selector('[data-testid="attach-menu-plus"]')
                if attach:
                    await attach.click()
                    await asyncio.sleep(0.5)

                file_input = await self._page.query_selector('input[type="file"]')
                if file_input:
                    await file_input.set_input_files(file_path)
                    await asyncio.sleep(1)
                    if caption:
                        cap_input = await self._page.query_selector('[data-testid="media-caption-input"]')
                        if cap_input:
                            await cap_input.fill(caption)
                    send_btn = await self._page.query_selector('[data-testid="send"]')
                    if send_btn:
                        await send_btn.click()
                    await asyncio.sleep(2)
                    return True, None

                return False, "Input de arquivo não encontrado"
            except Exception as exc:
                logger.error("send_file error: %s", exc)
                return False, str(exc)


class WhatsAppManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, WhatsAppSession] = {}
        self._rr_index: int = 0

    async def load_from_db(self, db) -> None:
        async with db.execute("SELECT id, nome FROM sessoes_wa") as cur:
            rows = await cur.fetchall()
        for row in rows:
            await self.add_session(row["id"], row["nome"])

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
        if sess:
            return sess.qr_data
        return None

    def get_status(self) -> list:
        return [
            {
                "id": sid,
                "nome": s.nome,
                "status": s.status,
                "phone": s.phone,
            }
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


wa_manager = WhatsAppManager()
