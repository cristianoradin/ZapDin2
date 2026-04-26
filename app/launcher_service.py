"""
ZapDin — Entry-point do Serviço Windows (ZapDin-App.exe)
=========================================================
Compilado com Nuitka/PyInstaller como ZapDin-App.exe.
Iniciado pelo NSSM como serviço Windows (sem janela/GUI).

Flags:
  --service       Modo padrão: uvicorn headless (usado pelo NSSM)
  --with-worker   Adiciona queue_worker no mesmo processo (modo dev/single)
  --open-activation  Abre a janela kiosk após subir (usado pelo instalador)
"""
from __future__ import annotations

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zapdin.service")


def _set_workdir() -> None:
    """Garante que o CWD é a pasta do executável."""
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    else:
        # Desenvolvimento: raiz do projeto (dois níveis acima deste arquivo)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.chdir(project_root)


def _wait_server(port: int, timeout: int = 30) -> bool:
    import time
    import urllib.request
    url = f"http://127.0.0.1:{port}/api/activate/status"
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> None:
    _set_workdir()

    args = sys.argv[1:]
    with_worker  = "--with-worker"      in args
    open_kiosk   = "--open-activation"  in args

    # Configura Playwright para usar os browsers bundled pelo instalador
    pw_browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if pw_browsers and os.path.isdir(pw_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_browsers
        logger.info("Playwright browsers: %s", pw_browsers)

    # Importa settings (lê o .env do CWD)
    from app.core.config import settings

    if with_worker:
        # Modo dev/single: injeta queue_worker no lifespan
        # Feito via monkey-patch antes do import do main app
        import app.main as _app_main
        from app.services import queue_worker as _qw

        _orig_lifespan = _app_main.lifespan

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lifespan_with_worker(app):
            async with _orig_lifespan(app):
                _qw.start()
                yield
                _qw.stop()

        _app_main.fastapi_app.router.lifespan_context = _lifespan_with_worker
        logger.info("Modo --with-worker: queue_worker ativado no mesmo processo.")

    logger.info("Iniciando ZapDin-App na porta %s (APP_STATE=%s)…",
                settings.port, settings.app_state)

    import uvicorn
    uvicorn_config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(uvicorn_config)

    if open_kiosk:
        # Sobe o servidor em background e abre o kiosk após ele estar online
        import threading
        import subprocess

        def _run_server():
            import asyncio
            asyncio.run(server.serve())

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()

        if _wait_server(settings.port):
            logger.info("Servidor online. Abrindo janela kiosk…")
            # Abre o launcher_gui.py como processo separado
            gui_exe = os.path.join(os.path.dirname(sys.executable), "ZapDin-Launcher.exe")
            if os.path.exists(gui_exe):
                subprocess.Popen([gui_exe])
            else:
                # Fallback: Edge/Chrome --app
                _open_browser_app(settings.port)
        else:
            logger.error("Timeout ao aguardar servidor. Kiosk não aberto.")

        t.join()
    else:
        import asyncio
        asyncio.run(server.serve())


def _open_browser_app(port: int) -> None:
    """Fallback: abre Edge/Chrome em modo --app sem barra de endereços."""
    import subprocess
    url = f"http://127.0.0.1:{port}"
    candidates = [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    no_win = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    for path in candidates:
        if os.path.exists(path):
            subprocess.Popen(
                [path, f"--app={url}", "--window-size=1280,820"],
                creationflags=no_win,
            )
            return


if __name__ == "__main__":
    main()
