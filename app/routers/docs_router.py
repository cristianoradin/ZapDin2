"""
ZapDin — Router de Documentação
================================
GET  /api/docs/erp          → retorna HTML da doc ERP como download
GET  /api/docs/abrir-erp    → abre doc ERP no browser padrão do SO (para kiosk)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse

from ..core.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/docs", tags=["docs"])

_STATIC_DIR = Path(__file__).parent.parent / "static"
_ERP_DOC = _STATIC_DIR / "doc-integracao-erp.html"
_PDV_DOC = _STATIC_DIR / "doc-integracao-pdv.html"


@router.get("/erp")
async def download_erp_doc(_: dict = Depends(get_current_user)):
    """
    Serve o HTML de integração ERP como download direto.
    O navegador faz o download do arquivo — o usuário pode abrir e
    usar 'Imprimir → Salvar como PDF' em qualquer browser.
    """
    if not _ERP_DOC.exists():
        return JSONResponse({"error": "Documento não encontrado."}, status_code=404)

    return FileResponse(
        path=str(_ERP_DOC),
        media_type="text/html",
        filename="ZapDin-Integracao-ERP.html",
        headers={"Content-Disposition": 'attachment; filename="ZapDin-Integracao-ERP.html"'},
    )


@router.get("/pdv")
async def download_pdv_doc(_: dict = Depends(get_current_user)):
    """Serve o HTML de integração PDV como download direto."""
    if not _PDV_DOC.exists():
        return JSONResponse({"error": "Documento não encontrado."}, status_code=404)
    return FileResponse(
        path=str(_PDV_DOC),
        media_type="text/html",
        filename="ZapDin-PDV-Integracao-ERP.html",
        headers={"Content-Disposition": 'attachment; filename="ZapDin-PDV-Integracao-ERP.html"'},
    )


@router.get("/abrir-pdv")
async def abrir_pdv_no_browser(_: dict = Depends(get_current_user)):
    """Abre o documento de integração PDV no browser padrão."""
    if not _PDV_DOC.exists():
        return JSONResponse({"error": "Documento não encontrado."}, status_code=404)
    doc_path = str(_PDV_DOC.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(doc_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", doc_path])
        else:
            subprocess.Popen(["xdg-open", doc_path])
        return {"ok": True}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/abrir-erp")
async def abrir_erp_no_browser(_: dict = Depends(get_current_user)):
    """
    Abre o documento de integração ERP no browser padrão do sistema operacional.
    Usado pelo kiosk (pywebview) que não suporta target="_blank".
    O usuário então usa Ctrl+P → Salvar como PDF no browser nativo.
    """
    if not _ERP_DOC.exists():
        return JSONResponse({"error": "Documento não encontrado."}, status_code=404)

    doc_path = str(_ERP_DOC.resolve())

    try:
        if sys.platform == "win32":
            os.startfile(doc_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", doc_path])
        else:
            subprocess.Popen(["xdg-open", doc_path])
        logger.info("[docs] Abrindo doc ERP no browser: %s", doc_path)
        return {"ok": True, "message": "Documento aberto no navegador padrão."}
    except Exception as exc:
        logger.error("[docs] Falha ao abrir doc ERP: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
