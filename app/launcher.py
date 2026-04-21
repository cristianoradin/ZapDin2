"""
Launcher ZapDin App
Inicia o servidor uvicorn em background e abre o navegador em modo app.
Compilado com PyInstaller --onefile --noconsole para gerar ZapDin-App.exe
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

PORT = 4000
APP_MODULE = "app.main:app"


def _root_dir() -> str:
    """Pasta pai do launcher — onde ficam app/ e o .venv/"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_python(root: str) -> str:
    candidates = [
        os.path.join(root, ".venv", "Scripts", "python.exe"),   # Windows venv
        os.path.join(root, ".venv", "bin", "python"),            # Unix venv
        sys.executable,
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return "python"


def _wait_server(timeout: int = 30) -> bool:
    url = f"http://127.0.0.1:{PORT}/login"
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _open_app_window() -> None:
    url = f"http://127.0.0.1:{PORT}"
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    for path in chrome_paths:
        if os.path.exists(path):
            subprocess.Popen(
                [path, f"--app={url}", "--window-size=1280,820"],
                creationflags=no_win,
            )
            return
    webbrowser.open(url)


def main() -> None:
    root = _root_dir()
    python = _find_python(root)
    no_win = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    log_path = os.path.join(root, "logs", "app.log")
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)

    with open(log_path, "a") as log:
        proc = subprocess.Popen(
            [python, "-m", "uvicorn", APP_MODULE,
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=root,
            stdout=log,
            stderr=log,
            creationflags=no_win,
        )

    if _wait_server():
        _open_app_window()
    else:
        webbrowser.open(f"http://127.0.0.1:{PORT}")

    proc.wait()


if __name__ == "__main__":
    main()
