# =============================================================================
#  ZapDin App — Instalador Windows (PowerShell)
#  Instala o app ZapDin como serviço Windows sem precisar compilar executáveis.
#
#  Pré-requisitos:
#    - Windows 10/11 x64
#    - PowerShell 5.1+ (já incluso no Windows 10/11)
#    - Acesso à internet
#    - Executar como Administrador
#
#  Uso:
#    Clique direito → "Executar com PowerShell como Administrador"
#    ou:
#    powershell -ExecutionPolicy Bypass -File install_windows.ps1
# =============================================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$APP_NAME       = "ZapDin"
$SERVICE_APP    = "ZapDinApp"
$SERVICE_WORKER = "ZapDinWorker"
$INSTALL_DIR    = "C:\ZapDin"
$GITHUB_REPO    = "cristianoradin/ZapDin2"
$NSSM_URL       = "https://nssm.cc/release/nssm-2.24.zip"
$PYTHON_URL     = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
$LOG_FILE       = "$INSTALL_DIR\logs\install.log"

# Token de acesso ao repositório GitHub (leitura)
# Deixe vazio se o repositório for público
$GITHUB_TOKEN   = "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"

# Endereço do Monitor pré-configurado (deixe vazio para perguntar ao instalar)
$MONITOR_URL_DEFAULT = "http://cloud.gruposgapetro.com.br:5000"

# ── Cores no terminal ──────────────────────────────────────────────────────────
function Write-Info  { param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "[ OK ]  $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[AVISO] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[ERRO]  $msg" -ForegroundColor Red; exit 1 }

function Write-Log {
    param($msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG_FILE -Value "[$ts] $msg" -Encoding UTF8
    Write-Host $msg
}

# ── Banner ─────────────────────────────────────────────────────────────────────
Clear-Host
Write-Host ""
Write-Host "  =============================================" -ForegroundColor Green
Write-Host "   ZapDin App — Instalador Windows" -ForegroundColor Green
Write-Host "   Envio automatico de mensagens WhatsApp" -ForegroundColor Green
Write-Host "  =============================================" -ForegroundColor Green
Write-Host ""

# ── Verificar admin ────────────────────────────────────────────────────────────
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Err "Execute este script como Administrador."
}
Write-OK "Executando como Administrador"

# ── Criar diretórios ───────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\tools" | Out-Null
Write-OK "Diretório: $INSTALL_DIR"

# ── Configuração do Monitor ────────────────────────────────────────────────────
if (-not [string]::IsNullOrWhiteSpace($MONITOR_URL_DEFAULT)) {
    # URL já pré-configurada no script — sem precisar digitar
    $MONITOR_URL = $MONITOR_URL_DEFAULT.TrimEnd('/')
    Write-OK "Monitor: $MONITOR_URL (pre-configurado)"
} else {
    Write-Host ""
    Write-Host "  Configuracao do servidor Monitor:" -ForegroundColor Yellow
    Write-Host "  (Obtenha o endereco com o suporte ZapDin)" -ForegroundColor Yellow
    Write-Host ""
    $MONITOR_URL = Read-Host "  Endereco do Monitor (ex: http://192.168.1.100:5000)"
    if ([string]::IsNullOrWhiteSpace($MONITOR_URL) -or $MONITOR_URL -eq "http://") {
        Write-Err "Endereco do monitor nao pode ser vazio."
    }
    $MONITOR_URL = $MONITOR_URL.TrimEnd('/')
}

# Se repositório privado e token não configurado no script, pedir ao usuário
if ([string]::IsNullOrWhiteSpace($GITHUB_TOKEN)) {
    Write-Host ""
    Write-Host "  Acesso ao repositorio GitHub:" -ForegroundColor Yellow
    Write-Host "  (Deixe em branco se o repositorio for publico)" -ForegroundColor Gray
    $inputToken = Read-Host "  Token GitHub (ghp_...)"
    if (-not [string]::IsNullOrWhiteSpace($inputToken)) {
        $GITHUB_TOKEN = $inputToken.Trim()
    }
}

# Montar URL do repositório com ou sem token
if ([string]::IsNullOrWhiteSpace($GITHUB_TOKEN)) {
    $REPO_URL = "https://github.com/$GITHUB_REPO.git"
} else {
    $REPO_URL = "https://git:${GITHUB_TOKEN}@github.com/$GITHUB_REPO.git"
}

Write-Host ""
Write-Log "MONITOR_URL configurado: $MONITOR_URL"

# ── Verificar/Instalar Python ──────────────────────────────────────────────────
Write-Info "Verificando Python..."
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "3\.(1[1-9]|[2-9]\d)") {
            $pythonCmd = $cmd
            Write-OK "Python encontrado: $ver ($cmd)"
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Info "Python 3.11+ nao encontrado. Instalando Python 3.12..."
    $pyInstaller = "$env:TEMP\python-installer.exe"
    Write-Info "Baixando Python de $PYTHON_URL..."
    Invoke-WebRequest -Uri $PYTHON_URL -OutFile $pyInstaller -UseBasicParsing
    Write-Info "Instalando Python (pode demorar alguns minutos)..."
    Start-Process -FilePath $pyInstaller `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" `
        -Wait -NoNewWindow
    Remove-Item $pyInstaller -Force

    # Atualizar PATH para encontrar o Python recém-instalado
    $env:PATH = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")

    foreach ($cmd in @("python", "python3")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "3\.") { $pythonCmd = $cmd; break }
        } catch {}
    }
    if (-not $pythonCmd) { Write-Err "Falha ao instalar Python. Instale manualmente em python.org" }
    Write-OK "Python instalado: $(&$pythonCmd --version)"
}

# ── Verificar/Instalar Git ─────────────────────────────────────────────────────
Write-Info "Verificando Git..."
try {
    $gitVer = git --version 2>&1
    Write-OK "Git encontrado: $gitVer"
} catch {
    Write-Info "Git nao encontrado. Instalando via winget..."
    try {
        winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
        $env:PATH = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path","User")
        Write-OK "Git instalado"
    } catch {
        Write-Err "Nao foi possivel instalar Git. Instale manualmente em git-scm.com"
    }
}

# ── Clonar / Atualizar repositório ────────────────────────────────────────────
Write-Info "Obtendo codigo fonte..."
if (Test-Path "$INSTALL_DIR\.git") {
    Write-Info "Repositorio existente — atualizando..."
    Set-Location $INSTALL_DIR
    git pull origin main 2>&1 | Write-Host
} else {
    Write-Info "Clonando repositorio..."
    if (Test-Path "$INSTALL_DIR\*") {
        # Diretório tem arquivos mas não é git — clonar para temp e mover
        $tmpDir = "$env:TEMP\zapdin_clone"
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        git clone $REPO_URL $tmpDir 2>&1 | Write-Host
        # Copiar conteúdo para INSTALL_DIR preservando logs/data
        Copy-Item "$tmpDir\*" $INSTALL_DIR -Recurse -Force
        Remove-Item $tmpDir -Recurse -Force
    } else {
        git clone $REPO_URL $INSTALL_DIR 2>&1 | Write-Host
    }
}
Write-OK "Codigo fonte obtido em $INSTALL_DIR"

# ── Criar virtualenv ───────────────────────────────────────────────────────────
Write-Info "Criando ambiente Python..."
Set-Location $INSTALL_DIR
$venv = "$INSTALL_DIR\app\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    & $pythonCmd -m venv $venv
    Write-OK "Virtualenv criado"
} else {
    Write-OK "Virtualenv existente reutilizado"
}

# ── Instalar dependências ─────────────────────────────────────────────────────
Write-Info "Instalando dependencias Python (pode demorar 2-5 min)..."
& "$venv\Scripts\pip.exe" install --upgrade pip --quiet
& "$venv\Scripts\pip.exe" install -r "$INSTALL_DIR\app\requirements.txt" --quiet
Write-OK "Dependencias instaladas"

# ── Instalar Playwright Chromium ──────────────────────────────────────────────
Write-Info "Instalando Playwright Chromium (pode demorar 5-10 min)..."
$env:PLAYWRIGHT_BROWSERS_PATH = "$INSTALL_DIR\playwright-browsers"
& "$venv\Scripts\python.exe" -m playwright install chromium 2>&1 | Write-Host
Write-OK "Playwright Chromium instalado"

# ── Baixar e instalar NSSM ────────────────────────────────────────────────────
Write-Info "Configurando NSSM (gerenciador de servicos Windows)..."
$nssmExe = "$INSTALL_DIR\tools\nssm.exe"
if (-not (Test-Path $nssmExe)) {
    $nssmZip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NSSM_URL -OutFile $nssmZip -UseBasicParsing
    Expand-Archive -Path $nssmZip -DestinationPath "$env:TEMP\nssm_extract" -Force
    Copy-Item "$env:TEMP\nssm_extract\nssm-2.24\win64\nssm.exe" $nssmExe -Force
    Remove-Item $nssmZip -Force
    Remove-Item "$env:TEMP\nssm_extract" -Recurse -Force
    Write-OK "NSSM instalado em $nssmExe"
} else {
    Write-OK "NSSM ja presente"
}

# ── Criar .env de bootstrap ───────────────────────────────────────────────────
Write-Info "Criando configuracao (.env)..."
$envFile = "$INSTALL_DIR\app\.env"
if (-not (Test-Path $envFile)) {
    $envContent = @"
# ZapDin App — gerado pelo instalador PowerShell
APP_STATE=locked
PORT=4000
DATABASE_URL=data\app.db
SECRET_KEY=

# Servidor Monitor
MONITOR_URL=$MONITOR_URL
MONITOR_CLIENT_TOKEN=
CLIENT_NAME=
CLIENT_CNPJ=
ERP_TOKEN=

# Playwright
PLAYWRIGHT_BROWSERS_PATH=$INSTALL_DIR\playwright-browsers

# Auto-update
VELOPACK_CHANNEL_URL=https://github.com/cristianoradin/ZapDin2/releases/latest/download
"@
    $envContent | Out-File -FilePath $envFile -Encoding UTF8
    Write-OK ".env criado (APP_STATE=locked — aguardando ativacao)"
} else {
    Write-Warn ".env existente preservado (reinstalacao)"
}

# ── Criar script de inicialização ─────────────────────────────────────────────
Write-Info "Criando scripts de servico..."

# start_app.bat — usado pelo NSSM para ZapDinApp
$startApp = @"
@echo off
cd /d "$INSTALL_DIR"
set PLAYWRIGHT_BROWSERS_PATH=$INSTALL_DIR\playwright-browsers
"$venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 4000
"@
$startApp | Out-File -FilePath "$INSTALL_DIR\tools\start_app.bat" -Encoding ASCII

# start_worker.bat — usado pelo NSSM para ZapDinWorker
$startWorker = @"
@echo off
cd /d "$INSTALL_DIR"
set PLAYWRIGHT_BROWSERS_PATH=$INSTALL_DIR\playwright-browsers
"$venv\Scripts\python.exe" app\worker_main.py
"@
$startWorker | Out-File -FilePath "$INSTALL_DIR\tools\start_worker.bat" -Encoding ASCII

Write-OK "Scripts de servico criados"

# ── Registrar serviços Windows via NSSM ──────────────────────────────────────
Write-Info "Registrando servicos Windows..."

# Parar e remover serviços anteriores (se existirem)
foreach ($svc in @($SERVICE_APP, $SERVICE_WORKER)) {
    $existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Warn "Servico $svc existente — removendo..."
        & $nssmExe stop $svc 2>&1 | Out-Null
        Start-Sleep 2
        & $nssmExe remove $svc confirm 2>&1 | Out-Null
    }
}

# ZapDinApp
& $nssmExe install $SERVICE_APP "$INSTALL_DIR\tools\start_app.bat"
& $nssmExe set $SERVICE_APP AppDirectory "$INSTALL_DIR"
& $nssmExe set $SERVICE_APP DisplayName "ZapDin — Backend API"
& $nssmExe set $SERVICE_APP Description "FastAPI + WhatsApp Web (porta 4000)"
& $nssmExe set $SERVICE_APP Start SERVICE_AUTO_START
& $nssmExe set $SERVICE_APP ObjectName LocalSystem
& $nssmExe set $SERVICE_APP AppStdout "$INSTALL_DIR\logs\app.stdout.log"
& $nssmExe set $SERVICE_APP AppStderr "$INSTALL_DIR\logs\app.stderr.log"
& $nssmExe set $SERVICE_APP AppRotateFiles 1
& $nssmExe set $SERVICE_APP AppRotateBytes 10485760
& $nssmExe set $SERVICE_APP AppExit Default Restart
& $nssmExe set $SERVICE_APP AppRestartDelay 5000

# ZapDinWorker
& $nssmExe install $SERVICE_WORKER "$INSTALL_DIR\tools\start_worker.bat"
& $nssmExe set $SERVICE_WORKER AppDirectory "$INSTALL_DIR"
& $nssmExe set $SERVICE_WORKER DisplayName "ZapDin — Worker"
& $nssmExe set $SERVICE_WORKER Description "Processa fila de mensagens WhatsApp"
& $nssmExe set $SERVICE_WORKER Start SERVICE_AUTO_START
& $nssmExe set $SERVICE_WORKER DependOnService $SERVICE_APP
& $nssmExe set $SERVICE_WORKER ObjectName LocalSystem
& $nssmExe set $SERVICE_WORKER AppStdout "$INSTALL_DIR\logs\worker.stdout.log"
& $nssmExe set $SERVICE_WORKER AppStderr "$INSTALL_DIR\logs\worker.stderr.log"
& $nssmExe set $SERVICE_WORKER AppExit Default Restart
& $nssmExe set $SERVICE_WORKER AppRestartDelay 8000

Write-OK "Servicos registrados"

# ── Regra de firewall ─────────────────────────────────────────────────────────
Write-Info "Configurando firewall (porta 4000)..."
netsh advfirewall firewall delete rule name="ZapDin App" 2>&1 | Out-Null
netsh advfirewall firewall add rule name="ZapDin App" dir=in action=allow protocol=TCP localport=4000 | Out-Null
Write-OK "Firewall configurado"

# ── Iniciar serviços ──────────────────────────────────────────────────────────
Write-Info "Iniciando servico ZapDinApp..."
& $nssmExe start $SERVICE_APP 2>&1 | Out-Null
Start-Sleep 5

$svc = Get-Service -Name $SERVICE_APP -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-OK "ZapDinApp rodando!"
    & $nssmExe start $SERVICE_WORKER 2>&1 | Out-Null
    Write-OK "ZapDinWorker iniciado"
} else {
    Write-Warn "ZapDinApp nao subiu automaticamente."
    Write-Warn "Verifique os logs em: $INSTALL_DIR\logs\app.stderr.log"
}

# ── Resumo final ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  =============================================" -ForegroundColor Green
Write-Host "   ZapDin App instalado!" -ForegroundColor Green
Write-Host "  =============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Instalado em:  $INSTALL_DIR" -ForegroundColor Cyan
Write-Host "  Monitor URL:   $MONITOR_URL" -ForegroundColor Cyan
Write-Host "  App URL:       http://localhost:4000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Proximos passos:" -ForegroundColor Yellow
Write-Host "  1. No Monitor, va em Clientes -> Novo Cliente -> Gerar Token" -ForegroundColor White
Write-Host "  2. Acesse http://localhost:4000" -ForegroundColor White
Write-Host "  3. Digite o Token de Ativacao na tela de ativacao" -ForegroundColor White
Write-Host ""
Write-Host "  Logs: $INSTALL_DIR\logs\" -ForegroundColor Gray
Write-Host ""

Write-Log "Instalacao concluida. MONITOR_URL=$MONITOR_URL"

Read-Host "  Pressione Enter para fechar"
