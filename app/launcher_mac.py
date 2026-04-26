"""
ZapDin — Launcher macOS
=======================
Inicia o servidor FastAPI em background e abre janela nativa sem barra de
endereços via pywebview (WKWebView / cocoa).

Ícone e nome do app são definidos via AppKit antes de o webview iniciar,
então o Dock e a barra de menus mostram "ZapDin" em vez de "Python".

Uso:
    app/.venv/bin/python app/launcher_mac.py

O processo reinicia automaticamente após ativação por token (servidor sai
com código 0).  Ctrl+C ou fechar a janela encerra tudo limpo.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("zapdin.launcher")

# ── Configurações ──────────────────────────────────────────────────────────────
ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON    = sys.executable
HOST      = "127.0.0.1"
PORT      = 4000
APP_URL   = f"http://{HOST}:{PORT}"
WIN_TITLE = "ZapDin"
APP_NAME  = "ZapDin"
ICON_PATH = os.path.join(ROOT, "app", "static", "logo", "Zapdin-removebg-preview.png")
WIN_W     = 1340
WIN_H     = 860
WIN_MIN   = (960, 660)
HEALTH    = f"{APP_URL}/api/activate/status"


# ── Identidade macOS ───────────────────────────────────────────────────────────

def _setup_macos_identity() -> None:
    """
    Define ícone e nome do processo ANTES de o webview abrir.
    Deve ser chamada no thread principal.

    - Dock: ícone ZapDin substituindo o foguete do Python
    - Barra de menus: nome "ZapDin" em vez de "Python"
    - Activity Monitor: nome do processo atualizado
    """
    if sys.platform != "darwin":
        return

    # ── Nome do processo (Activity Monitor / ps) ──────────────────────────
    try:
        import ctypes, ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # macOS: setprogname(name)
        setprogname = getattr(libc, "setprogname", None)
        if setprogname:
            setprogname.argtypes = [ctypes.c_char_p]
            setprogname(APP_NAME.encode())
    except Exception as e:
        logger.debug("setprogname: %s", e)

    # ── AppKit: ícone no Dock + nome na barra de menus ────────────────────
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        app = NSApplication.sharedApplication()

        # Ícone do Dock
        if os.path.exists(ICON_PATH):
            img = NSImage.alloc().initByReferencingFile_(ICON_PATH)
            if img and img.isValid():
                app.setApplicationIconImage_(img)
                logger.info("Ícone do Dock configurado.")
            else:
                logger.warning("Imagem inválida: %s", ICON_PATH)
        else:
            logger.warning("Ícone não encontrado: %s", ICON_PATH)

        # Nome na barra de menus (CFBundleName)
        try:
            info = NSBundle.mainBundle().infoDictionary()
            info["CFBundleName"]        = APP_NAME
            info["CFBundleDisplayName"] = APP_NAME
            logger.info("Nome do app configurado: %s", APP_NAME)
        except (TypeError, KeyError) as e:
            logger.debug("CFBundleName: %s", e)

    except ImportError:
        logger.debug("pyobjc não disponível — identidade macOS não configurada.")
    except Exception as e:
        logger.debug("AppKit identity error: %s", e)


# ── Utilitários ────────────────────────────────────────────────────────────────

def _kill_port(port: int) -> None:
    """Libera a porta caso outro processo a esteja usando."""
    try:
        pids = subprocess.check_output(
            ["lsof", "-ti", f"tcp:{port}"], text=True
        ).strip().split()
        for pid in pids:
            os.kill(int(pid), signal.SIGKILL)
        time.sleep(0.6)
    except Exception:
        pass


def _wait_server(url: str, timeout: int = 45) -> bool:
    """Aguarda o servidor FastAPI responder."""
    for _ in range(timeout * 4):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def _start_server() -> subprocess.Popen:
    """Inicia uvicorn em background e retorna o processo."""
    _kill_port(PORT)
    return subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn",
            "app.main:app",
            "--host", HOST,
            "--port", str(PORT),
        ],
        cwd=ROOT,
        stdout=open(os.path.join(ROOT, "app_startup.log"), "a"),
        stderr=subprocess.STDOUT,
    )


# ── Ciclo principal ────────────────────────────────────────────────────────────

def _run_once() -> bool:
    """
    Inicia o servidor + janela uma vez.
    Retorna True  se deve reiniciar (ativação → exit 0).
    Retorna False se deve encerrar.
    """
    logger.info("Iniciando servidor ZapDin em %s…", APP_URL)
    server = _start_server()

    if not _wait_server(HEALTH):
        logger.error("Servidor não respondeu. Verifique app_startup.log.")
        server.terminate()
        return False

    logger.info("Servidor online. Abrindo janela…")

    # Configura identidade macOS antes de criar a janela
    _setup_macos_identity()

    _restart_flag = [False]

    def _watch_server(proc, flag):
        proc.wait()
        code = proc.returncode
        logger.info("Servidor encerrou (código %s).", code)
        if code == 0:
            flag[0] = True
        try:
            import webview as _wv
            _wv.destroy_all()
        except Exception:
            pass

    try:
        import webview

        window = webview.create_window(
            title=WIN_TITLE,
            url=APP_URL,
            width=WIN_W,
            height=WIN_H,
            min_size=WIN_MIN,
            resizable=True,
            text_select=False,
            easy_drag=False,
        )

        watcher = threading.Thread(
            target=_watch_server,
            args=(server, _restart_flag),
            daemon=True,
        )
        watcher.start()

        webview.start(debug=False, private_mode=False)

    except ImportError:
        logger.warning("pywebview não instalado. Usando fallback --app.")
        _fallback_browser(APP_URL)
    except Exception as exc:
        logger.error("Erro ao abrir janela: %s. Usando fallback.", exc)
        _fallback_browser(APP_URL)
    finally:
        server.terminate()
        try:
            server.wait(timeout=4)
        except subprocess.TimeoutExpired:
            server.kill()

    return _restart_flag[0]


def _fallback_browser(url: str) -> None:
    """Abre Chrome ou Edge em modo --app (sem barra de endereços)."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            subprocess.Popen([path, f"--app={url}", f"--window-size={WIN_W},{WIN_H}"])
            input("Pressione Enter para encerrar o servidor…")
            return
    import webbrowser
    webbrowser.open(url)
    input("Pressione Enter para encerrar o servidor…")


# ── Entry-point ────────────────────────────────────────────────────────────────

def main() -> None:
    while True:
        try:
            should_restart = _run_once()
        except KeyboardInterrupt:
            logger.info("Encerrado pelo usuário.")
            break

        if should_restart:
            logger.info("Reiniciando após ativação…")
            time.sleep(1)
        else:
            break

    logger.info("ZapDin encerrado.")


if __name__ == "__main__":
    main()
