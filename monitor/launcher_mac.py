"""
ZapDin Monitor — Launcher macOS
================================
Inicia o servidor do monitor em background e abre janela nativa sem barra
de endereços via pywebview (WKWebView / cocoa).

Ícone e nome do app são definidos via AppKit antes de o webview iniciar,
então o Dock e a barra de menus mostram "ZapDin Monitor" em vez de "Python".

Uso:
    monitor/.venv/bin/python monitor/launcher_mac.py
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
logger = logging.getLogger("zapdin.monitor.launcher")

# ── Configurações ──────────────────────────────────────────────────────────────
ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON    = sys.executable
HOST      = "127.0.0.1"
PORT      = 5000
APP_URL   = f"http://{HOST}:{PORT}"
WIN_TITLE = "ZapDin Monitor"
APP_NAME  = "ZapDin Monitor"
# Logo compartilhada: está em app/static/logo/
ICON_PATH = os.path.join(ROOT, "app", "static", "logo", "Zapdin-removebg-preview.png")
WIN_W     = 1380
WIN_H     = 880
WIN_MIN   = (1024, 700)
HEALTH    = f"{APP_URL}/api/auth/me"   # retorna 401 → servidor está de pé


# ── Identidade macOS ───────────────────────────────────────────────────────────

def _setup_macos_identity() -> None:
    """
    Define ícone e nome do processo ANTES de o webview abrir.
    Deve ser chamada no thread principal.
    """
    if sys.platform != "darwin":
        return

    # ── Nome do processo (Activity Monitor / ps) ──────────────────────────
    try:
        import ctypes, ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
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
    """Aguarda o servidor responder (qualquer código HTTP = vivo)."""
    for _ in range(timeout * 4):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.HTTPError:
            return True          # 401/403/etc → servidor está de pé
        except Exception:
            time.sleep(0.25)
    return False


def _start_server() -> subprocess.Popen:
    _kill_port(PORT)
    return subprocess.Popen(
        [
            PYTHON, "-m", "uvicorn",
            "monitor.main:app",
            "--host", HOST,
            "--port", str(PORT),
        ],
        cwd=ROOT,
        stdout=open(os.path.join(ROOT, "monitor_startup.log"), "a"),
        stderr=subprocess.STDOUT,
    )


# ── Ciclo principal ────────────────────────────────────────────────────────────

def _run() -> None:
    logger.info("Iniciando servidor Monitor em %s…", APP_URL)
    server = _start_server()

    if not _wait_server(HEALTH):
        logger.error("Servidor não respondeu. Verifique monitor_startup.log.")
        server.terminate()
        return

    logger.info("Servidor online. Abrindo janela…")

    # Configura identidade macOS antes de criar a janela
    _setup_macos_identity()

    def _watch(proc):
        proc.wait()
        logger.info("Servidor monitor encerrou (código %s).", proc.returncode)
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

        threading.Thread(target=_watch, args=(server,), daemon=True).start()

        webview.start(debug=False, private_mode=True)

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


def _fallback_browser(url: str) -> None:
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
    try:
        _run()
    except KeyboardInterrupt:
        logger.info("Encerrado pelo usuário.")
    logger.info("ZapDin Monitor encerrado.")


if __name__ == "__main__":
    main()
