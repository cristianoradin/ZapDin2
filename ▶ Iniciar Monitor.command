#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/monitor/.venv/bin/python"
LAUNCHER="$ROOT/monitor/launcher_mac.py"

# ── Verifica venv ──────────────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
  echo "ERRO: venv do monitor não encontrado em monitor/.venv"
  echo "Execute: cd ~/Zapdin2/monitor && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -p "Pressione Enter para fechar..."
  exit 1
fi

# ── Instala pywebview + pyobjc automaticamente se necessário ─────────────────
if ! "$PYTHON" -c "import webview" 2>/dev/null; then
  echo "[$(date '+%H:%M:%S')] Instalando pywebview (primeira vez)..."
  "$PYTHON" -m pip install --quiet "pywebview>=5.0.0"
fi
if [[ "$(uname)" == "Darwin" ]] && ! "$PYTHON" -c "from AppKit import NSApplication" 2>/dev/null; then
  echo "[$(date '+%H:%M:%S')] Instalando pyobjc (necessário para ícone/nome do app)..."
  "$PYTHON" -m pip install --quiet pyobjc-framework-Cocoa
fi

echo "=== ZapDin Monitor ==="
echo "Pasta : $ROOT"
echo "Python: $PYTHON"
echo ""

exec "$PYTHON" "$LAUNCHER"
