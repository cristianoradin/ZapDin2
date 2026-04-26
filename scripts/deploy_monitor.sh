#!/usr/bin/env bash
# ============================================================
#  ZapDin Monitor — Script de Deploy/Atualização no Servidor
# ============================================================
# Uso: bash scripts/deploy_monitor.sh
#
# O que este script faz:
#   1. Faz git pull origin main
#   2. Instala/atualiza dependências Python
#   3. Reinicia o serviço (systemd ou processo direto)
#
# Pré-requisitos no servidor:
#   - Git configurado (SSH key ou token já autenticado)
#   - monitor/.venv/ criado (veja INSTALL.md)
#   - Variável ZAPDIN_DIR apontando para a pasta do projeto
#     ou execute de dentro da pasta do projeto

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
echo "=============================================="
echo "  ZapDin Monitor — Deploy"
echo "  Diretório: $PROJECT_DIR"
echo "  Data: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ── 1. Git pull ────────────────────────────────────────────────────────────────
echo ""
echo "[1/3] Atualizando código do repositório..."
git fetch origin
git pull origin main
echo "     Código atualizado."

# ── 2. Dependências Python ─────────────────────────────────────────────────────
echo ""
echo "[2/3] Instalando dependências..."
VENV="$PROJECT_DIR/monitor/.venv"
if [ ! -d "$VENV" ]; then
    echo "     Criando virtualenv em $VENV..."
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$PROJECT_DIR/monitor/requirements.txt"
echo "     Dependências OK."

# ── 3. Reinicia serviço ────────────────────────────────────────────────────────
echo ""
echo "[3/3] Reiniciando serviço..."

# Detecta se está rodando como systemd
if systemctl is-active --quiet zapdin-monitor 2>/dev/null; then
    sudo systemctl restart zapdin-monitor
    echo "     Serviço reiniciado via systemd."
else
    # Reinicio via PID file ou kill + start direto
    PID_FILE="$PROJECT_DIR/monitor.pid"
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            kill "$OLD_PID"
            echo "     Processo anterior ($OLD_PID) encerrado."
            sleep 2
        fi
        rm -f "$PID_FILE"
    fi

    # Inicia em background
    nohup "$VENV/bin/python" -m uvicorn monitor.main:app \
        --host 0.0.0.0 \
        --port 5000 \
        --log-level info \
        >> "$PROJECT_DIR/monitor_startup.log" 2>&1 &

    echo $! > "$PID_FILE"
    echo "     Monitor iniciado (PID: $(cat "$PID_FILE"))."
fi

echo ""
echo "Deploy concluído com sucesso!"
echo "Log: tail -f $PROJECT_DIR/monitor_startup.log"
