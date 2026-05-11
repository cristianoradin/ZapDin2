#!/usr/bin/env bash
# Deploy ZapDin2 → cloud.gruposgapetro.com.br via git pull
# Uso: bash deploy.sh
set -e

SERVER="master@cloud.gruposgapetro.com.br"
PORT="22110"
SSH="ssh -p $PORT -o StrictHostKeyChecking=no"
APP_DIR="/home/master/Zapdin2"
MON_DIR="/opt/zapdin2/monitor"

echo "=== Deploy ZapDin2 ==="
echo "Servidor: $SERVER:$PORT"
echo ""

echo "[1/2] Atualizando código no servidor..."
$SSH $SERVER bash -s <<'REMOTE'
set -e

# App
cd /home/master/Zapdin2
git pull origin main
echo "  App: $(git log --oneline -1)"

# Monitor (se for subdiretório do mesmo repo ou repositório separado)
if [ -d /opt/zapdin2/monitor/.git ]; then
  cd /opt/zapdin2/monitor
  git pull origin main
  echo "  Monitor: $(git log --oneline -1)"
elif [ -d /opt/zapdin2/.git ]; then
  cd /opt/zapdin2
  git pull origin main
  echo "  Monitor (opt): $(git log --oneline -1)"
fi

echo "[2/2] Reiniciando serviços..."
sudo systemctl restart zapdin-app.service zapdin-monitor.service
sleep 2
echo ""
echo "Status dos serviços:"
systemctl is-active zapdin-app.service zapdin-monitor.service
REMOTE

echo ""
echo "=== Deploy concluído! ==="
echo "App:     https://zapdin.gruposgapetro.com.br"
echo "Monitor: https://cloud.gruposgapetro.com.br"
