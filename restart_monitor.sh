#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "Diretório: $ROOT"

# Mata processo na porta 5000 se existir
PID=$(lsof -ti :5000 2>/dev/null || true)
if [ -n "$PID" ]; then
  echo "Encerrando PID $PID..."
  kill $PID
  sleep 1
fi

cd "$ROOT"

# Detecta o python certo
if [ -f "monitor/.venv/bin/python" ]; then
  PYTHON="monitor/.venv/bin/python"
elif [ -f "monitor/.venv/bin/python3" ]; then
  PYTHON="monitor/.venv/bin/python3"
else
  PYTHON="python3"
fi

echo "Usando: $PYTHON"
echo "Testando import..."
$PYTHON -c "import sys; sys.path.insert(0,'.'); from monitor.main import app; print('Import OK')"

echo ""
echo "Iniciando servidor em http://localhost:5000 ..."
$PYTHON -m uvicorn monitor.main:app --host 0.0.0.0 --port 5000
