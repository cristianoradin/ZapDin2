# ZapDin — Release Notes

## v2.0.0

**Instalador Inteligente v3**

- Instalação silenciosa de dependências (WebView2, VC++ 2022)
- Dois serviços Windows independentes via NSSM: Backend (ZapDinApp) e Worker (ZapDinWorker)
- First-Run Experience: janela kiosk sem barra de endereços (WebView2)
- Ativação por token: config entregue cifrada com AES-256-GCM pelo Monitor
- Auto-atualização via Velopack (delta packages)
- Worker standalone com anti-ban: delays aleatórios, spintax, limite diário, horário de funcionamento
- Código compilado com Nuitka (proteção de fonte)

## v1.0.0

- Versão inicial
- FastAPI + WhatsApp Web via Playwright
- Integração ERP via webhook
- Painel de monitoramento central (Monitor)
- Notificações Telegram
- GitHub Actions: build automático de instaladores Windows
