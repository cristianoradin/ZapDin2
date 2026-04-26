# ZapDin — Guia de Instalação em Produção

> **Arquitetura**: dois componentes independentes que se comunicam por API HTTP.
> - **Monitor** → instalado no servidor do desenvolvedor (Linux/Mac/Windows)
> - **App** → instalado na máquina do cliente (Windows)

---

## Pré-requisitos

| Componente | Requisito |
|---|---|
| Monitor (servidor) | Python 3.11+, Git, porta 5000 liberada |
| App (cliente) | Windows 10/11 x64, porta 4000 liberada na rede local |
| Rede | Cliente deve alcançar o servidor na porta 5000 |

---

## PARTE 1 — Servidor: Instalar o Monitor

### 1.1 — Clonar o repositório

```bash
git clone https://github.com/cristianoradin/ZapDin2.git
cd ZapDin2
```

### 1.2 — Criar virtualenv e instalar dependências

```bash
python3 -m venv monitor/.venv
monitor/.venv/bin/pip install --upgrade pip
monitor/.venv/bin/pip install -r monitor/requirements.txt
```

### 1.3 — Configurar o .env do Monitor

```bash
cp monitor/.env.example monitor/.env
nano monitor/.env
```

Preencha **obrigatoriamente**:

```env
PORT=5000
DATABASE_URL=data/monitor.db
SECRET_KEY=<gere com: python3 -c "import secrets; print(secrets.token_hex(32))">

# URL pública deste servidor — o que o App usará para se conectar
# Use o IP fixo ou domínio do servidor
MONITOR_PUBLIC_URL=http://SEU_IP_OU_DOMINIO:5000

# Token compartilhado Monitor ↔ App (para sincronização de usuários)
# Gere um valor aleatório e anote — será usado também no app/.env
APP_SYNC_TOKEN=<gere com: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
```

### 1.4 — Criar a pasta de dados

```bash
mkdir -p data
```

### 1.5 — Testar o monitor manualmente

```bash
cd ~/ZapDin2
monitor/.venv/bin/python -m uvicorn monitor.main:app --host 0.0.0.0 --port 5000
```

Acesse `http://SEU_IP:5000` — deve aparecer a tela de login.
Login padrão: **cristiano / radin123** (troque a senha após o primeiro acesso).

### 1.6 — Instalar como serviço systemd (Linux — recomendado)

```bash
sudo bash scripts/setup_monitor_service.sh
```

Verifica se está rodando:
```bash
sudo systemctl status zapdin-monitor
sudo journalctl -u zapdin-monitor -f
```

---

## PARTE 2 — Monitor: Cadastrar o cliente

Antes de instalar o app no cliente, cadastre-o no Monitor:

1. Acesse o painel do Monitor
2. Vá em **Clientes** → **Novo Cliente**
3. Preencha nome e CNPJ
4. Clique em **Gerar Token de Ativação** — anote o token (formato `XXXX-XXXX-XXXX-XXXX`)
5. O **token de heartbeat** do cliente (campo `token`) será o `APP_SYNC_TOKEN` — anote também

---

## PARTE 3 — Cliente Windows: Instalar o App

### 3.1 — Baixar o instalador

Baixe o arquivo `ZapDin-Setup-X.X.X.exe` na página de releases do GitHub:
```
https://github.com/cristianoradin/ZapDin2/releases/latest
```

### 3.2 — Executar o instalador

1. Execute `ZapDin-Setup-X.X.X.exe` como **Administrador**
2. Na tela **"Configuração do Servidor Monitor"**, informe o endereço do monitor:
   ```
   http://IP_DO_SERVIDOR:5000
   ```
3. Conclua a instalação normalmente

### 3.3 — Ativar o sistema

1. O sistema abre automaticamente na tela de ativação
2. Digite o **Token de Ativação** gerado no Monitor (passo 2.4)
3. O app se conecta ao monitor, recebe as configurações e reinicia ativo

### 3.4 — Verificar serviços Windows

Abra o **Gerenciador de Serviços** (`services.msc`) e confirme:
- `ZapDinApp` — Em execução, Início: Automático
- `ZapDinWorker` — Em execução, Início: Automático

Ou via PowerShell:
```powershell
Get-Service ZapDinApp, ZapDinWorker
```

---

## PARTE 4 — Firewall

### Servidor (Monitor)

```bash
# Ubuntu/Debian (ufw)
sudo ufw allow 5000/tcp
sudo ufw reload

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload
```

### Cliente Windows

O instalador cria uma regra de firewall automaticamente para a porta 4000.
Se necessário, adicione manualmente:
```
netsh advfirewall firewall add rule name="ZapDin App" dir=in action=allow protocol=TCP localport=4000
```

---

## PARTE 5 — Atualizar

### Monitor (servidor)

```bash
cd ~/ZapDin2
bash scripts/deploy_monitor.sh
```

O script faz `git pull`, atualiza dependências e reinicia o serviço.

### App (cliente Windows)

O app verifica atualizações automaticamente a cada 15 minutos.
Quando uma nova versão for publicada no GitHub Releases, ele baixa e aplica no próximo restart.

Para publicar uma nova versão:
1. Atualize a versão em `app/versao.json`
2. Faça commit e push para `main`
3. O GitHub Actions compila e publica automaticamente no GitHub Releases
4. O monitor é notificado via `POST /api/versao/whatsapp` (ou atualize manualmente no painel)

---

## Senhas padrão

| Sistema | Usuário | Senha | **Troque após instalar!** |
|---|---|---|---|
| Monitor | cristiano | radin123 | ✅ Sim |
| App | admin | admin | ✅ Sim |

---

## Diagnóstico rápido

```bash
# Ver logs do monitor (Linux/servidor)
sudo journalctl -u zapdin-monitor -n 100 --no-pager

# Testar conectividade do cliente para o monitor (no Windows)
curl http://SEU_IP:5000/api/versao/whatsapp

# Verificar status do app (no Windows)
curl http://localhost:4000/api/activate/status
```

---

## Checklist de instalação

- [ ] Monitor instalado e acessível na URL configurada
- [ ] `MONITOR_PUBLIC_URL` configurado com o IP/domínio real (não localhost)
- [ ] `APP_SYNC_TOKEN` configurado em `monitor/.env`
- [ ] Cliente cadastrado no Monitor com Token de Ativação gerado
- [ ] Instalador executado no cliente com URL do monitor correta
- [ ] App ativado com o token (status = active)
- [ ] Serviços `ZapDinApp` e `ZapDinWorker` rodando no cliente
- [ ] Firewall liberado nas duas pontas (porta 5000 servidor, 4000 cliente)
- [ ] Senhas padrão trocadas em ambos os sistemas
