#!/bin/bash
cd ~/Zapdin2
rm -f .git/index.lock
TOKEN="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"
echo "=== Adicionando Cloudflare Tunnel ==="
git add -f scripts/setup_cloudflare_tunnel.sh cloudflared/ INSTALL.md
git status --short
git commit -m "feat: Cloudflare Tunnel — setup sem porta aberta no servidor" 2>&1 || echo "[nada novo]"
echo "=== Push ==="
GIT_TERMINAL_PROMPT=0 git -c credential.helper='' push "https://git:${TOKEN}@github.com/cristianoradin/ZapDin2.git" main 2>&1
echo "=== CONCLUIDO ==="
read -p "Enter para fechar..."
