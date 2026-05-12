"""
evolution_service.py — integração com Evolution API (open source WhatsApp REST API).

Usa webhooks para receber QR e status em tempo real, sem polling instável.
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
_file_tokens: Dict[str, str] = {}
_file_tokens_lock = threading.Lock()


def _url(path: str) -> str:
    return f"{settings.evolution_url.rstrip('/')}/{path.lstrip('/')}"


def _h() -> dict:
    return {"apikey": settings.evolution_api_key, "Content-Type": "application/json"}


def _instance_name(empresa_id: int, session_id: str) -> str:
    return f"e{empresa_id}_{session_id}"


def _webhook_url() -> str:
    """URL que a Evolution API vai chamar quando houver eventos."""
    return f"http://127.0.0.1:{settings.port}/api/evo-webhook"


# ── Sessão local ──────────────────────────────────────────────────────────────

class EvoSession:
    def __init__(self, session_id: str, nome: str, empresa_id: int):
        self.session_id = session_id
        self.nome = nome
        self.empresa_id = empresa_id
        self.status = "disconnected"
        self.qr_data: Optional[str] = None
        self.phone: Optional[str] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ── Webhook handlers (chamados pelo EvoManager) ───────────────────────────

    def on_qr_updated(self, qr_base64: str):
        """Recebe QR em tempo real via webhook."""
        if not qr_base64.startswith("data:"):
            qr_base64 = "data:image/png;base64," + qr_base64
        self.qr_data = qr_base64
        self.status = "disconnected"
        logger.info("EvoSession [%s] QR atualizado via webhook", self.session_id)

    def on_connection_update(self, state: str, phone: Optional[str] = None):
        """Recebe mudança de estado em tempo real via webhook."""
        prev = self.status
        if state == "open":
            self.status = "connected"
            self.qr_data = None      # limpa QR após conectar
            if phone:
                self.phone = phone
        elif state in ("connecting", "pairingCode"):
            self.status = "connecting"
            # Após escanear o QR, começa a checar ativamente até confirmar "open"
            asyncio.create_task(self._poll_until_open())
        else:
            self.status = "disconnected"

        if prev != self.status:
            logger.info("EvoSession [%s] %s → %s", self.session_id, prev, self.status)

    async def _poll_until_open(self):
        """Checa connectionState a cada 2s por até 30s após o QR ser escaneado."""
        for _ in range(15):
            await asyncio.sleep(2)
            if self.status == "connected":
                return
            try:
                await self._check_state()
            except Exception:
                pass
            if self.status == "connected":
                return

    # ── Heartbeat leve — apenas confirma estado a cada 60s ───────────────────

    def start_heartbeat(self):
        if not self._heartbeat_task or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def stop_heartbeat(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self):
        """Confirma estado real a cada 60s. Não gera QR — isso é responsabilidade do webhook."""
        await asyncio.sleep(3)   # checagem rápida logo no início
        while True:
            try:
                await self._check_state()
            except Exception as exc:
                logger.debug("EvoSession heartbeat [%s]: %s", self.session_id, exc)
            await asyncio.sleep(60 if self.status == "connected" else 15)

    async def _check_state(self):
        inst = _instance_name(self.empresa_id, self.session_id)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_url(f"instance/connectionState/{inst}"), headers=_h())
        if r.status_code != 200:
            return
        data = r.json()
        state = (
            data.get("instance", {}).get("state")
            or data.get("state")
            or "close"
        )
        self.on_connection_update(state)

    async def fetch_qr_now(self):
        """Solicita QR ou confirma estado conectado (chamado no startup e quando front abre a página)."""
        inst = _instance_name(self.empresa_id, self.session_id)
        try:
            # Primeiro verifica estado real — pode já estar conectado
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                rs = await client.get(_url(f"instance/connectionState/{inst}"), headers=_h())
            if rs.status_code == 200:
                state = (
                    rs.json().get("instance", {}).get("state")
                    or rs.json().get("state")
                    or "close"
                )
                if state == "open":
                    self.on_connection_update("open")
                    return   # já conectado, não precisa gerar QR

            if self.status == "connected":
                return

            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(_url(f"instance/connect/{inst}"), headers=_h())
            if r.status_code == 200:
                d = r.json()
                qr = (
                    d.get("base64")
                    or d.get("qrcode", {}).get("base64")
                    or d.get("qr", "")
                )
                if qr:
                    self.on_qr_updated(qr)
        except Exception as exc:
            logger.debug("fetch_qr_now [%s]: %s", self.session_id, exc)


# ── Manager ───────────────────────────────────────────────────────────────────

class EvoManager:
    def __init__(self):
        self._sessions: Dict[str, EvoSession] = {}
        self._rr_index = 0
        # Índice inverso: instanceName → EvoSession (para despacho de webhook)
        self._inst_index: Dict[str, EvoSession] = {}

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
        self._inst_index[inst] = sess
        sess.start_heartbeat()
        # Busca estado atual e QR imediatamente
        asyncio.create_task(sess.fetch_qr_now())
        logger.info("EvoManager: sessão %s empresa %s", session_id, empresa_id)

    async def remove_session(self, session_id: str, empresa_id: int) -> None:
        key = self._key(empresa_id, session_id)
        sess = self._sessions.pop(key, None)
        if not sess:
            return
        sess.stop_heartbeat()
        inst = _instance_name(empresa_id, session_id)
        self._inst_index.pop(inst, None)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                await client.delete(_url(f"instance/delete/{inst}"), headers=_h())
        except Exception as exc:
            logger.debug("remove_session erro: %s", exc)

    async def stop(self) -> None:
        for sess in list(self._sessions.values()):
            sess.stop_heartbeat()

    # ── Webhook handler (chamado pelo endpoint POST /api/evo-webhook) ─────────

    def handle_webhook(self, payload: dict) -> None:
        """Processa evento recebido da Evolution API em tempo real."""
        event = (payload.get("event") or "").upper()
        inst = payload.get("instance") or payload.get("instanceName") or ""
        data = payload.get("data") or {}

        sess = self._inst_index.get(inst)
        if not sess:
            return

        if event in ("QRCODE_UPDATED", "QRCODE.UPDATED"):
            qr = (
                data.get("base64")
                or data.get("qrcode", {}).get("base64")
                or ""
            )
            if qr:
                sess.on_qr_updated(qr)

        elif event in ("CONNECTION_UPDATE", "CONNECTION.UPDATE"):
            state = data.get("state") or data.get("instance", {}).get("state") or ""
            phone = (
                data.get("wuid")
                or data.get("phone")
                or data.get("number")
                or None
            )
            if state:
                sess.on_connection_update(state, phone)

    # ── Provisiona instância + webhook na Evolution API ───────────────────────

    async def _ensure_instance(self, inst: str) -> bool:
        wh_url = _webhook_url()
        webhook_cfg = {
            "url": wh_url,
            "byEvents": False,
            "base64": False,
            "events": ["QRCODE_UPDATED", "CONNECTION_UPDATE"],
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                # Checa se já existe
                r = await client.get(_url("instance/fetchInstances"), headers=_h())
                if r.status_code == 200:
                    existentes = {
                        i.get("instance", {}).get("instanceName")
                        for i in r.json()
                    }
                    if inst in existentes:
                        # Atualiza webhook (caso URL tenha mudado)
                        await client.post(
                            _url(f"webhook/set/{inst}"),
                            json=webhook_cfg,
                            headers=_h(),
                        )
                        return True

                # Cria nova instância já com webhook configurado
                r2 = await client.post(
                    _url("instance/create"),
                    json={
                        "instanceName": inst,
                        "qrcode": True,
                        "integration": "WHATSAPP-BAILEYS",
                        "webhook": webhook_cfg,
                    },
                    headers=_h(),
                )
                logger.info("Evolution create %s → %s", inst, r2.status_code)
                if r2.status_code in (200, 201):
                    # Configura webhook separadamente também (compatibilidade v1/v2)
                    await client.post(
                        _url(f"webhook/set/{inst}"),
                        json=webhook_cfg,
                        headers=_h(),
                    )
                    return True
                return False
        except Exception as exc:
            logger.error("_ensure_instance [%s]: %s", inst, exc)
            return False

    # ── Interface pública ─────────────────────────────────────────────────────

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
        # Se não tiver QR, solicita geração imediata (não-bloqueante)
        if not sess.qr_data and sess.status != "connected":
            asyncio.create_task(sess.fetch_qr_now())
        return sess.qr_data

    def get_status(self, empresa_id: int) -> list:
        prefix = f"{empresa_id}:"
        return [
            {"id": k.split(":", 1)[1], "nome": s.nome, "status": s.status, "phone": s.phone}
            for k, s in self._sessions.items()
            if k.startswith(prefix)
        ]

    # ── Envio de texto ────────────────────────────────────────────────────────

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

    # ── Envio de arquivo ──────────────────────────────────────────────────────

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

        # Envia sempre via base64 — mais confiável que URL (evita race condition de token)
        return await self._send_file_b64(inst, number, file_path, filename, mime, mtype, caption)

    async def _send_file_b64(
        self, inst, number, file_path, filename, mime, mtype, caption
    ) -> Tuple[bool, Optional[str]]:
        """Fallback: envia como data URI base64."""
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
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
        pass


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
