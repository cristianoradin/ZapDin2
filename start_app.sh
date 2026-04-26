#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Mata processo na porta 4000 se existir
PID=$(lsof -ti tcp:4000 2>/dev/null)
if [ -n "$PID" ]; then
  echo "Encerrando processo anterior (PID $PID)..."
  kill -9 $PID 2>/dev/null
  sleep 1
fi

PYTHON="$ROOT/app/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
  echo "ERRO: venv não encontrado em app/.venv"
  echo "Tente: cd ~/Zapdin2/app && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "Iniciando ZapDin App em http://localhost:4000 ..."
exec "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 4000
