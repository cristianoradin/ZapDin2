"""
evolution_service.py — integração com Evolution API (open source WhatsApp REST API).

Expõe a mesma interface do wa_manager (whatsapp_service.py):
  - load_from_db, add_session, remove_session
  - pick_session, get_qr, get_status
  - send_text, send_file, schedule_status_check

Documentação Evolution API: https://github.com/EvolutionAPI/evolution-api
"""
import asyncio
import base64
import logging
import os
import secrets
import threading
from typing import Dict, Optional, Tuple

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0

# ── Tokens temporários para servir arquivos à Evolution API ──────────────────
# Mapa token → caminho absoluto do arquivo; limpo após uso ou TTL curto
_file_tokens: Dict[str, str] = {}
_file_tokens_lock = threading.Lock()


def _url(path: str) -> str:
    return f"{settings.evolution_url.rstrip('/')}/{path.lstrip('/')}"


def _h() -> dict:
    return {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}


def _instance_name(empresa_id: int, session_id: str) -> str:
    return f"e{empresa_id}_{session_id}"


# ── Sessão local ──────────────────────────────────────────────────────────────

class EvoSession:
    def __init__(self, session_id: str, nome: str, empresa_id: int):
        self.session_id = session_id
        self.nome = nome
        self.empresa_id = empresa_id
        self.status = "disconnected"
        self.qr_data: Optional[str] = None
        self.phone: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._qr_requested = True   # busca QR logo no primeiro poll

    def start_polling(self):
        if not self._poll_task or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    def stop_polling(self):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    def request_qr(self):
        """Marca que o front pediu o QR — próximo poll fará a chamada /connect."""
        self._qr_requested = True

    async def _poll_loop(self):
        while True:
            try:
                await self._refresh_status()
            except Exception as exc:
                logger.debug("EvoSession poll [%s]: %s", self.session_id, exc)
            # Conectado: checa a cada 30s. Desconectado: checa estado a cada 8s.
            await asyncio.sleep(30 if self.status == "connected" else 8)

    async def _refresh_status(self):
        inst = _instance_name(self.empresa_id, self.session_id)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                _url(f"instance/connectionState/{inst}"), headers=_h()
            )
            if r.status_code == 200:
                data = r.json()
                # Suporte a diferentes formatos da Evolution API v1/v2
                state = (
                    data.get("instance", {}).get("state")
                    or data.get("state")
                    or "close"
                )
                if state == "open":
                    self.status = "connected"
                    self.qr_data = None
                    self._qr_requested = False
                    logger.info("EvoSession [%s] conectado", self.session_id)
                    return
                prev = self.status
                self.status = "connecting" if state == "connecting" else "disconnected"
                if prev != self.status:
                    logger.info("EvoSession [%s] estado: %s", self.session_id, self.status)

            # Busca QR apenas quando solicitado pelo front e não conectado
            if self._qr_requested and self.status != "connected":
                self._qr_requested = False
                r2 = await client.get(_url(f"instance/connect/{inst}"), headers=_h())
                if r2.status_code == 200:
                    d = r2.json()
                    qr = (
                        d.get("base64")
                        or d.get("qrcode", {}).get("base64")
                        or d.get("qr", "")
                    )
                    if qr and not qr.startswith("data:"):
                        qr = "data:image/png;base64," + qr
                    self.qr_data = qr or None
                    logger.info("EvoSession [%s] QR gerado", self.session_id)


# ── Manager ───────────────────────────────────────────────────────────────────

class EvoManager:
    def __init__(self):
        self._sessions: Dict[str, EvoSession] = {}
        self._rr_index = 0

    def _key(self, empresa_id: int, session_id: str) -> str:
        return f"{empresa_id}:{session_id}"

    async def load_from_db(self, db) -> None:
        async with db.execute("SELECT id, nome, empresa_id FROM sessoes_wa") as cur:
            rows = await cur.fetchall()
        for row in rows:
            await self.add_session(row["id"], row["nome"], row["empresa_id"])

    async def add_session(self, session_id: str, nome: str, empresa_id: int) -> None:
        key = self._key(empresa_id, session_id)
        if key in self._sessions:
            return
        inst = _instance_name(empresa_id, session_id)
        await self._ensure_instance(inst)
        sess = EvoSession(session_id, nome, empresa_id)
        self._sessions[key] = sess
        sess.start_polling()
        logger.info("EvoManager: sessão %s empresa %s", session_id, empresa_id)

    async def remove_session(self, session_id: str, empresa_id: int) -> None:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.pop(key, None)
        if not sess:
            return
        sess.stop_polling()
        inst = _instance_name(empresa_id, session_id)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.delete(_url(f"instance/delete/{inst}"), headers=_h())
        except Exception as exc:
            logger.debug("remove_session erro: %s", exc)

    async def stop(self) -> None:
        for sess in list(self._sessions.values()):
            sess.stop_polling()

    async def _ensure_instance(self, inst: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(_url("instance/fetchInstances"), headers=_h())
                if r.status_code == 200:
                    existentes = [
                        i.get("instance", {}).get("instanceName")
                        for i in r.json()
                    ]
                    if inst in existentes:
                        return True
                r2 = await client.post(
                    _url("instance/create"),
                    json={"instanceName": inst, "qrcode": True, "integration": "WHATSAPP-BAILEYS"},
                    headers=_h(),
                )
                logger.info("Evolution create %s → %s", inst, r2.status_code)
                return r2.status_code in (200, 201)
        except Exception as exc:
            logger.error("_ensure_instance [%s]: %s", inst, exc)
            return False

    def pick_session(self, empresa_id: int) -> Optional[str]:
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
        sess = self._sessions.get(self._key(empresa_id, session_id))
        if not sess:
            return None
        # Sinaliza que o front quer o QR — o próximo poll vai buscá-lo
        if sess.status != "connected":
            sess.request_qr()
        return sess.qr_data

    def get_status(self, empresa_id: int) -> list:
        prefix = f"{empresa_id}:"
        return [
            {"id": k.split(":", 1)[1], "nome": s.nome, "status": s.status, "phone": s.phone}
            for k, s in self._sessions.items()
            if k.startswith(prefix)
        ]

    async def send_text(
        self, session_id: str, empresa_id: int, phone: str, message: str
    ) -> Tuple[bool, Optional[str]]:
        inst = _instance_name(empresa_id, session_id)
        number = phone.strip().lstrip("+").replace(" ", "")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    _url(f"message/sendText/{inst}"),
                    json={"number": number, "text": message},
                    headers=_h(),
                )
            if r.status_code in (200, 201):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            return False, str(exc)

    async def send_file(
        self,
        session_id: str,
        empresa_id: int,
        phone: str,
        file_path: str,
        filename: str,
        caption: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        inst = _instance_name(empresa_id, session_id)
        number = phone.strip().lstrip("+").replace(" ", "")
        ext = os.path.splitext(filename)[1].lower()
        mtype = _media_type(ext)
        mime = _mimetype(ext)

        # ── Gera token temporário e URL acessível pela Evolution API ─────────
        token = secrets.token_urlsafe(24)
        with _file_tokens_lock:
            _file_tokens[token] = file_path

        # A Evolution API buscará o arquivo via localhost para evitar problemas
        # com base64 muito grande no corpo JSON (especialmente para PDFs/docs).
        serve_url = f"http://127.0.0.1:{settings.port}/api/evo-file/{token}"

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    _url(f"message/sendMedia/{inst}"),
                    json={
                        "number": number,
                        "mediatype": mtype,
                        "mimetype": mime,
                        "caption": caption or "",
                        "media": serve_url,   # Evolution API faz GET nesta URL
                        "fileName": filename,
                    },
                    headers=_h(),
                )
            if r.status_code in (200, 201):
                logger.info("EvoManager send_file OK: %s → %s", filename, number)
                return True, None
            # Fallback: tenta via base64 direto
            logger.warning(
                "EvoManager send_file URL falhou (%s), tentando base64: %s",
                r.status_code, r.text[:200],
            )
            return await self._send_file_b64(inst, number, file_path, filename, mime, mtype, caption)
        except Exception as exc:
            logger.error("EvoManager send_file exc: %s", exc)
            return await self._send_file_b64(inst, number, file_path, filename, mime, mtype, caption)
        finally:
            with _file_tokens_lock:
                _file_tokens.pop(token, None)

    async def _send_file_b64(
        self, inst, number, file_path, filename, mime, mtype, caption
    ) -> Tuple[bool, Optional[str]]:
        """Fallback: envia o arquivo como base64 com prefixo data URI."""
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode()
            data_uri = f"data:{mime};base64,{b64}"
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    _url(f"message/sendMedia/{inst}"),
                    json={
                        "number": number,
                        "mediatype": mtype,
                        "mimetype": mime,
                        "caption": caption or "",
                        "media": data_uri,
                        "fileName": filename,
                    },
                    headers=_h(),
                )
            if r.status_code in (200, 201):
                logger.info("EvoManager send_file b64 OK: %s → %s", filename, number)
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            return False, str(exc)

    def schedule_status_check(self, arquivo_id, session_id, empresa_id, phone):
        pass  # Evolution API não suporta polling de status de entrega


# ── MIME helpers ──────────────────────────────────────────────────────────────

def _media_type(ext: str) -> str:
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return "image"
    if ext in {".mp4", ".avi", ".mov", ".mkv"}:
        return "video"
    if ext in {".mp3", ".ogg", ".wav", ".m4a", ".opus"}:
        return "audio"
    return "document"


def _mimetype(ext: str) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
        ".wav": "audio/wav", ".m4a": "audio/mp4", ".opus": "audio/opus",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip": "application/zip",
    }.get(ext, "application/octet-stream")


# ── Instância global ──────────────────────────────────────────────────────────
evo_manager = EvoManager()
