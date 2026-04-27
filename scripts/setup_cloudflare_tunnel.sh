#!/usr/bin/env bash
# ============================================================
#  ZapDin Monitor — Setup Cloudflare Tunnel
# ============================================================
# Instala e configura cloudflared para expor o monitor via
# Cloudflare Tunnel. Nenhuma porta precisa ser aberta no servidor.
#
# Pré-requisitos:
#   - Domínio cadastrado no Cloudflare (ex: seudominio.com)
#   - Conta Cloudflare com acesso ao domínio
#   - Monitor já instalado e rodando em localhost:5000
#
# Uso: bash scripts/setup_cloudflare_tunnel.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TUNNEL_NAME="zapdin-monitor"
CF_DIR="$HOME/.cloudflared"
CONFIG_FILE="$CF_DIR/config.yml"
SERVICE_NAME="zapdin-tunnel"

# ── Cores ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[AVISO]${NC} $*"; }
error()   { echo -e "${RED}[ERRO]${NC} $*"; exit 1; }

echo "=============================================="
echo "  ZapDin — Cloudflare Tunnel Setup"
echo "  Nenhuma porta será aberta no servidor!"
echo "=============================================="
echo ""

# ── 1. Detectar OS e instalar cloudflared ──────────────────
install_cloudflared() {
    if command -v cloudflared &>/dev/null; then
        info "cloudflared já instalado: $(cloudflared --version)"
        return
    fi

    info "Instalando cloudflared..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="amd64" ;;
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm"   ;;
        *)       error "Arquitetura não suportada: $ARCH" ;;
    esac

    # Tenta via apt (Debian/Ubuntu)
    if command -v apt-get &>/dev/null; then
        curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
            sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
        echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
            https://pkg.cloudflare.com/cloudflared any main" | \
            sudo tee /etc/apt/sources.list.d/cloudflared.list
        sudo apt-get update -qq
        sudo apt-get install -y cloudflared
    else
        # Download direto como fallback
        LATEST=$(curl -s https://api.github.com/repos/cloudflare/cloudflared/releases/latest \
            | grep tag_name | cut -d'"' -f4)
        URL="https://github.com/cloudflare/cloudflared/releases/download/${LATEST}/cloudflared-linux-${CF_ARCH}"
        sudo curl -fsSL "$URL" -o /usr/local/bin/cloudflared
        sudo chmod +x /usr/local/bin/cloudflared
    fi

    info "cloudflared instalado: $(cloudflared --version)"
}

# ── 2. Autenticar com Cloudflare ───────────────────────────
authenticate() {
    if [ -f "$CF_DIR/cert.pem" ]; then
        info "Já autenticado com Cloudflare."
        return
    fi

    echo ""
    warn "Será aberta uma URL de autenticação no navegador."
    warn "Faça login na sua conta Cloudflare e selecione o domínio."
    echo ""
    read -p "Pressione Enter para continuar..."
    cloudflared tunnel login
}

# ── 3. Criar o túnel ───────────────────────────────────────
create_tunnel() {
    # Verifica se túnel já existe
    if cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
        info "Túnel '$TUNNEL_NAME' já existe."
        TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
    else
        info "Criando túnel '$TUNNEL_NAME'..."
        cloudflared tunnel create "$TUNNEL_NAME"
        TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
    fi

    info "Tunnel ID: $TUNNEL_ID"
    echo "$TUNNEL_ID" > "$PROJECT_DIR/.tunnel_id"
}

# ── 4. Configurar hostname ─────────────────────────────────
configure_hostname() {
    echo ""
    echo "Qual hostname você quer usar para o monitor?"
    echo "Exemplo: monitor.seudominio.com"
    echo "(O domínio deve estar cadastrado no Cloudflare)"
    echo ""
    read -p "Hostname: " HOSTNAME

    if [ -z "$HOSTNAME" ]; then
        error "Hostname não pode ser vazio."
    fi

    echo "$HOSTNAME" > "$PROJECT_DIR/.tunnel_hostname"

    info "Criando registro DNS no Cloudflare..."
    cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME"
    info "DNS configurado: $HOSTNAME → tunnel"
}

# ── 5. Gerar config.yml ────────────────────────────────────
generate_config() {
    TUNNEL_ID=$(cat "$PROJECT_DIR/.tunnel_id")
    HOSTNAME=$(cat "$PROJECT_DIR/.tunnel_hostname")
    CRED_FILE="$CF_DIR/${TUNNEL_ID}.json"

    mkdir -p "$CF_DIR"
    cat > "$CONFIG_FILE" << EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED_FILE}

ingress:
  - hostname: ${HOSTNAME}
    service: http://localhost:5000
    originRequest:
      noTLSVerify: false
  - service: http_status:404
EOF

    info "Config gerado em: $CONFIG_FILE"
}

# ── 6. Instalar como serviço systemd ──────────────────────
install_service() {
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    TUNNEL_ID=$(cat "$PROJECT_DIR/.tunnel_id")

    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=ZapDin — Cloudflare Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
ExecStart=/usr/local/bin/cloudflared tunnel --config ${CONFIG_FILE} run ${TUNNEL_NAME}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"

    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Serviço '$SERVICE_NAME' iniciado com sucesso!"
    else
        error "Serviço não iniciou. Verifique: sudo journalctl -u $SERVICE_NAME -f"
    fi
}

# ── 7. Atualizar monitor/.env ──────────────────────────────
update_env() {
    HOSTNAME=$(cat "$PROJECT_DIR/.tunnel_hostname")
    ENV_FILE="$PROJECT_DIR/monitor/.env"
    PUBLIC_URL="https://${HOSTNAME}"

    if [ -f "$ENV_FILE" ]; then
        if grep -q "^MONITOR_PUBLIC_URL=" "$ENV_FILE"; then
            sed -i "s|^MONITOR_PUBLIC_URL=.*|MONITOR_PUBLIC_URL=${PUBLIC_URL}|" "$ENV_FILE"
        else
            echo "MONITOR_PUBLIC_URL=${PUBLIC_URL}" >> "$ENV_FILE"
        fi
        info "monitor/.env atualizado: MONITOR_PUBLIC_URL=${PUBLIC_URL}"
    else
        warn "monitor/.env não encontrado. Configure manualmente:"
        warn "  MONITOR_PUBLIC_URL=${PUBLIC_URL}"
    fi
}

# ── Main ───────────────────────────────────────────────────
main() {
    install_cloudflared
    authenticate
    create_tunnel
    configure_hostname
    generate_config
    install_service
    update_env

    HOSTNAME=$(cat "$PROJECT_DIR/.tunnel_hostname")

    echo ""
    echo "=============================================="
    echo "  Cloudflare Tunnel configurado com sucesso!"
    echo "=============================================="
    echo ""
    echo "  URL do monitor: https://${HOSTNAME}"
    echo "  Porta 5000:     FECHADA (sem exposição direta)"
    echo ""
    echo "Comandos úteis:"
    echo "  Status:  sudo systemctl status ${SERVICE_NAME}"
    echo "  Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
    echo "  Parar:   sudo systemctl stop ${SERVICE_NAME}"
    echo "  Restart: sudo systemctl restart ${SERVICE_NAME}"
    echo ""
    echo "Configure o app com:"
    echo "  MONITOR_URL=https://${HOSTNAME}"
    echo ""
}

main
