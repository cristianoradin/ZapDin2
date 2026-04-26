#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/app/.venv/bin/python"
LAUNCHER="$ROOT/app/launcher_mac.py"

# ── Verifica venv ──────────────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
  echo "ERRO: venv não encontrado em app/.venv"
  echo "Execute: cd ~/Zapdin2/app && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -p "Pressione Enter para fechar..."
  exit 1
fi

# ── Instala pywebview + pyobjc automaticamente se necessário ─────────────────
if ! "$PYTHON" -c "import webview" 2>/dev/null; then
  echo "[$(date '+%H:%M:%S')] Instalando pywebview (primeira vez)..."
  "$PYTHON" -m pip install --quiet "pywebview>=5.0.0"
fi
# pyobjc é necessário no macOS para configurar ícone e nome do app
if [[ "$(uname)" == "Darwin" ]] && ! "$PYTHON" -c "from AppKit import NSApplication" 2>/dev/null; then
  echo "[$(date '+%H:%M:%S')] Instalando pyobjc (necessário para ícone/nome do app)..."
  "$PYTHON" -m pip install --quiet pyobjc-framework-Cocoa
fi

echo "=== ZapDin App ==="
echo "Pasta : $ROOT"
echo "Python: $PYTHON"
echo ""

exec "$PYTHON" "$LAUNCHER"
