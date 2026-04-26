"""
ZapDin — Launcher Kiosk (ZapDin-Launcher.exe)
===============================================
Abre uma janela nativa sem barra de endereços usando pywebview + WebView2.
Compilado separadamente como ZapDin-Launcher.exe pelo CI.

Requisito: Microsoft Edge WebView2 Runtime instalado (garantido pelo instalador).
           pip install pywebview
"""
from __future__ import annotations

import os
import sys
import time
import logging
import urllib.request

logger = logging.getLogger("zapdin.launcher")

APP_URL  = "http://127.0.0.1:4000"
WIN_W    = 1280
WIN_H    = 820
WIN_MIN  = (900, 640)


def _wait_server(url: str, timeout: int = 45) -> bool:
    """Aguarda o servidor FastAPI estar respondendo."""
    health = f"{url}/api/activate/status"
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(health, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _read_port() -> int:
    """Lê a porta configurada no .env (fallback: 4000)."""
    if getattr(sys, "frozen", False):
        env_file = os.path.join(os.path.dirname(sys.executable), ".env")
    else:
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

    if not os.path.exists(env_file):
        return 4000

    for line in open(env_file, encoding="utf-8"):
        line = line.strip()
        if line.startswith("PORT=") or line.startswith("APP_PORT="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return 4000


def main() -> None:
    port = _read_port()
    url  = f"http://127.0.0.1:{port}"

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("Aguardando servidor em %s…", url)

    if not _wait_server(url):
        logger.error("Servidor não respondeu em %s. Encerrando launcher.", url)
        sys.exit(1)

    logger.info("Servidor online. Abrindo janela ZapDin…")

    try:
        import webview  # pywebview

        window = webview.create_window(
            title="ZapDin",
            url=url,
            width=WIN_W,
            height=WIN_H,
            min_size=WIN_MIN,
            resizable=True,
            text_select=False,
            # Desabilita menu de contexto (botão direito) para parecer app nativo
            easy_drag=False,
        )

        # Impede navegação para domínios externos (segurança kiosk)
        def _on_navigating(event):
            if url not in event.get("url", ""):
                # Permite apenas rotas do próprio servidor
                target = event.get("url", "")
                if not (target.startswith(f"http://127.0.0.1:{port}") or
                        target.startswith(f"http://localhost:{port}")):
                    logger.warning("Navegação bloqueada: %s", target)
                    return False
            return True

        # gui="edgechromium" é exclusivo do Windows (WebView2).
        # No macOS usa WKWebView automaticamente.
        kwargs = {"debug": False, "private_mode": False}
        if sys.platform == "win32":
            kwargs["gui"] = "edgechromium"

        webview.start(**kwargs)

    except ImportError:
        logger.warning("pywebview não disponível. Usando fallback Edge/Chrome --app.")
        _fallback_browser(url)
    except Exception as exc:
        logger.error("Erro ao abrir janela WebView2: %s. Usando fallback.", exc)
        _fallback_browser(url)


def _fallback_browser(url: str) -> None:
    """Fallback: Edge/Chrome em modo --app (sem barra de endereços)."""
    import subprocess
    candidates = [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    no_win = 0x08000000 if sys.platform == "win32" else 0
    for path in candidates:
        if os.path.exists(path):
            subprocess.Popen(
                [path, f"--app={url}", f"--window-size={WIN_W},{WIN_H}"],
                creationflags=no_win,
            )
            return
    # Último recurso
    import webbrowser
    webbrowser.open(url)


if __name__ == "__main__":
    main()
