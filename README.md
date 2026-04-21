# ZapDin 2 — Python

Sistema de envio de mensagens WhatsApp e painel de monitoramento central, reconstruído em Python com FastAPI.

## Estrutura

```
zapdin2/
├── app/        # App WhatsApp (porta 4000)
└── monitor/    # Painel admin central (porta 5000)
```

## App WhatsApp (app/)

```bash
cd app
cp .env.example .env
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

Acesse: http://localhost:4000 — Login padrão: `admin` / `admin`

## Monitor Central (monitor/)

```bash
cd monitor
cp .env.example .env
pip install -r requirements.txt
uvicorn monitor.main:app --host 0.0.0.0 --port 5000 --reload
```

Acesse: http://localhost:5000 — Login padrão: `cristiano` / `radin123`

## Endpoints principais

### App (porta 4000)
| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/api/auth/login` | Login |
| GET | `/api/stats` | Estatísticas do dashboard |
| POST | `/api/erp/venda` | Recebe venda e envia WhatsApp |
| POST | `/api/erp/arquivo` | Recebe PDF em base64 e envia |
| GET/POST | `/api/config` | Configuração de mensagem |
| GET/POST | `/api/sessoes` | Gerenciar sessões WhatsApp |
| GET | `/api/qr/{id}` | QR Code como data URL |

### Monitor (porta 5000)
| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/api/auth/login` | Login admin |
| GET | `/api/monitor` | Status dos postos em tempo real |
| GET/POST | `/api/clientes` | Gerenciar postos |
| POST | `/api/report` | Heartbeat dos postos |
| GET/POST | `/api/versao/whatsapp` | Gerenciar versão do app |

## GitHub Actions

O workflow `.github/workflows/build-installer.yml` cria automaticamente:
- Zips de cada app
- Instaladores `.exe` via Inno Setup (Windows)
- GitHub Release com todos os artefatos

Trigger: push em `main` com mudanças em `app/`, `monitor/`, `setup_app.iss` ou `setup_monitor.iss`.
