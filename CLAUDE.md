# CLAUDE.md — ZapDin2

Referência rápida para orientação do agente. Leia este arquivo no início de cada sessão.

---

## Estrutura do projeto

```
Zapdin2/
├── app/                        # Sistema de envio (porta 4000)
│   ├── main.py                 # FastAPI + Socket.IO + LockMiddleware
│   ├── core/
│   │   ├── activation.py       # Crypto AES-256-GCM lazy imports (IMPORTANTE: imports lazy!)
│   │   ├── config.py           # Settings via pydantic-settings — lê app/.env
│   │   ├── database.py         # aiosqlite — DB em app/data/app.db
│   │   └── security.py        # Cookie de sessão (SESSION_COOKIE)
│   ├── routers/
│   │   ├── activation.py       # POST /api/activate, GET /activate
│   │   ├── internal.py         # /internal/* — apenas localhost
│   │   ├── auth.py             # Login/logout
│   │   ├── whatsapp.py         # Sessões WA, QR, send-text
│   │   ├── erp.py              # Integração ERP
│   │   ├── config_router.py    # Config geral
│   │   ├── arquivos.py         # Upload de arquivos
│   │   ├── stats.py            # Estatísticas
│   │   └── telegram_router.py  # Bot Telegram
│   ├── services/
│   │   ├── whatsapp_service.py # WAManager — Playwright
│   │   ├── queue_worker.py     # Worker (roda SEPARADO como ZapDinWorker)
│   │   ├── reporter.py         # Relatórios periódicos
│   │   ├── updater.py          # Velopack auto-update
│   │   └── telegram_service.py # Telegram bot
│   ├── static/                 # Frontend SPA (index.html, login.html, logo/)
│   ├── .env                    # Configuração ativa (APP_STATE, PORT, etc.)
│   ├── .venv/                  # Python 3.13 — SEMPRE usar este venv
│   ├── launcher.py             # Launcher GUI (PyInstaller)
│   ├── launcher_gui.py         # Janela kiosk (pywebview)
│   ├── launcher_service.py     # Serviço NSSM
│   └── worker_main.py          # Entry-point do Worker standalone
│
├── monitor/                    # Painel administrativo (porta 5000)
│   ├── main.py                 # FastAPI
│   ├── core/
│   │   ├── config.py           # Settings — lê monitor/.env
│   │   ├── database.py         # aiosqlite — DB em monitor/data/monitor.db
│   │   └── security.py
│   ├── routers/
│   │   ├── auth.py             # Login + CRUD usuários (/api/auth/usuarios)
│   │   ├── clientes.py         # CRUD clientes + activation_token
│   │   ├── activation.py       # Gera token de ativação
│   │   ├── monitor_router.py   # Dados de monitoramento
│   │   └── versoes.py          # Gestão de versões
│   ├── static/                 # Frontend monitor (index.html, login.html) — TEMA CLARO
│   └── .venv/                  # Python 3.13 — SEMPRE usar este venv
│
├── data/                       # Dados compartilhados (se houver)
├── installer/                  # Scripts Inno Setup (.iss)
├── diagnostico.py              # Diagnóstico: python diagnostico.py
├── start_app.sh                # Inicia app (mata porta 4000, usa app/.venv)
├── restart_monitor.sh          # Inicia monitor (mata porta 5000, usa monitor/.venv)
├── ▶ Iniciar App.command       # Double-click no Finder para iniciar app
└── ▶ Iniciar Monitor.command   # Double-click no Finder para iniciar monitor
```

---

## Como iniciar os serviços

```bash
# App (porta 4000)
cd ~/Zapdin2
app/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 4000

# Monitor (porta 5000)
cd ~/Zapdin2
monitor/.venv/bin/python -m uvicorn monitor.main:app --host 0.0.0.0 --port 5000

# Ou double-click nos .command no Finder
```

**NUNCA usar sistema Python** — só `app/.venv/bin/python` e `monitor/.venv/bin/python`.

---

## app/.env (configuração ativa)

```
APP_STATE=active          # 'active' ou 'locked'. Se 'locked', LockMiddleware bloqueia tudo
PORT=4000
DATABASE_URL=data/app.db
SECRET_KEY=dev-secret-key-zapdin2
MONITOR_URL=http://localhost:5000
MONITOR_CLIENT_TOKEN=token-teste
CLIENT_NAME=Posto Teste
```

---

## Regras críticas de arquitetura

1. **`app/core/activation.py`** — imports de `cryptography` são LAZY (dentro de `_crypto_imports()`). Nunca mova para o topo do arquivo — causa ImportError no startup se o Python errado for usado.

2. **`LockMiddleware`** — bloqueia todas as rotas se `APP_STATE != active`. Prefixos permitidos: `/activate`, `/api/activate`, `/login`, `/static/`, `/logo/`, `/favicon`.

3. **`queue_worker`** — NÃO inicia junto com o app. Roda como processo separado (ZapDinWorker via NSSM). Para dev one-process: descomente a linha em `app/main.py`.

4. **Socket.IO** — o `app` retornado em `app/main.py` é `socketio.ASGIApp(sio, other_asgi_app=fastapi_app)`, não o FastAPI diretamente.

5. **Autenticação** — cookie de sessão em ambos os sistemas. Endpoint `/api/logout` (POST) apaga o cookie.

---

## Design system (frontend)

```css
--accent: #3d7f1f;
--accent-light: #7cdc44;
--bg: #f4f6f9;
--surface: #ffffff;
--border: #e4e6ea;
--text: #1a1d23;
--text-muted: #6b7280;
--grad: linear-gradient(90deg, #3d7f1f 0%, #7cdc44 50%, #3b82f6 100%);
```

Fonte: Inter (Google Fonts). Logo: 240px na tela de login.

---

## Dependências principais

| Pacote | Uso |
|---|---|
| fastapi + uvicorn | Servidor HTTP |
| python-socketio | WebSocket (Socket.IO) |
| aiosqlite | Banco SQLite assíncrono |
| pydantic-settings | Config via .env |
| playwright | Automação WhatsApp Web |
| cryptography | Ativação AES-256-GCM (lazy import!) |
| itsdangerous | Cookies de sessão assinados |
| httpx | HTTP client assíncrono |
| python-jose | JWT (se usado) |

---

## Fluxo de ativação (licença)

1. Monitor gera `activation_token` para o cliente
2. Monitor chama `encrypt_config(token, config)` → blob cifrado AES-256-GCM
3. App recebe blob via `/api/activate`, chama `decrypt_config(token, blob)` → grava `.env` → `APP_STATE=active`

---

## Banco de dados

- **App**: `app/data/app.db` (SQLite) — sessões WA, config, filas de envio
- **Monitor**: `monitor/data/monitor.db` (SQLite) — clientes, usuários, versões, tokens

Ambos inicializam via `init_db()` no lifespan do FastAPI.
