#!/usr/bin/env bash
# ============================================================
#  ZapDin Monitor — Instalar como serviço systemd (Linux)
# ============================================================
# Uso: sudo bash scripts/setup_monitor_service.sh
#
# Cria o serviço zapdin-monitor no systemd para iniciar
# automaticamente com o servidor e reiniciar em caso de falha.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/monitor/.venv"
SERVICE_NAME="zapdin-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Detecta usuário atual (não root)
RUN_USER="${SUDO_USER:-$(whoami)}"
if [ "$RUN_USER" = "root" ]; then
    echo "AVISO: Execute como usuário normal com sudo, não diretamente como root."
    echo "  Ex: sudo bash scripts/setup_monitor_service.sh"
    RUN_USER="ubuntu"  # fallback padrão em servidores Ubuntu
fi

echo "=============================================="
echo "  ZapDin Monitor — Configurar Serviço"
echo "  Diretório: $PROJECT_DIR"
echo "  Usuário: $RUN_USER"
echo "=============================================="

if [ ! -d "$VENV" ]; then
    echo "ERRO: Virtualenv não encontrado em $VENV"
    echo "Execute primeiro: python3 -m venv monitor/.venv && monitor/.venv/bin/pip install -r monitor/requirements.txt"
    exit 1
fi

echo ""
echo "Criando arquivo de serviço systemd..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ZapDin Monitor — Painel Administrativo
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${VENV}/bin/python -m uvicorn monitor.main:app --host 0.0.0.0 --port 5000 --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=zapdin-monitor

# Reinicia automaticamente em caso de falha
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
EOF

echo "Habilitando e iniciando serviço..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "Serviço '$SERVICE_NAME' iniciado com sucesso!"
    echo ""
    echo "Comandos úteis:"
    echo "  Status:  sudo systemctl status $SERVICE_NAME"
    echo "  Logs:    sudo journalctl -u $SERVICE_NAME -f"
    echo "  Parar:   sudo systemctl stop $SERVICE_NAME"
    echo "  Restart: sudo systemctl restart $SERVICE_NAME"
else
    echo "ERRO: Serviço não iniciou. Verifique:"
    systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi
