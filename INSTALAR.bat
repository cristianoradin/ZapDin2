@echo off
:: ============================================================
::  ZapDin App — Instalador Windows
::  Versao 1.1 — Duplo clique para instalar. Requer internet.
:: ============================================================
title ZapDin — Instalador

:: Elevar para Administrador automaticamente
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo  =============================================
echo   ZapDin App - Instalando...
echo  =============================================
echo.

set INSTALL_DIR=C:\ZapDin
set MONITOR_URL=http://zapdin.gruposgapetro.com.br:5000
set PYTHON_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe
set NSSM_EXE=%INSTALL_DIR%\tools\nssm.exe

:: ─── Criar estrutura de pastas ────────────────────────────────────────────
if not exist "%INSTALL_DIR%"           mkdir "%INSTALL_DIR%"
if not exist "%INSTALL_DIR%\logs"      mkdir "%INSTALL_DIR%\logs"
if not exist "%INSTALL_DIR%\data"      mkdir "%INSTALL_DIR%\data"
if not exist "%INSTALL_DIR%\tools"     mkdir "%INSTALL_DIR%\tools"

:: ─── 1/7 Copiar arquivos ─────────────────────────────────────────────────
echo [1/7] Copiando arquivos do app...
xcopy /E /I /Y "%~dp0app" "%INSTALL_DIR%\app" >nul 2>&1
echo       OK

:: ─── 2/7 Verificar / instalar Python ─────────────────────────────────────
echo [2/7] Verificando Python...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo       Python nao encontrado. Instalando Python 3.12...
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%TEMP%\python-installer.exe' -UseBasicParsing"
    "%TEMP%\python-installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del "%TEMP%\python-installer.exe"
    set "PATH=%PATH%;C:\Program Files\Python312;C:\Program Files\Python312\Scripts"
    echo       Python 3.12 instalado.
) else (
    echo       Python OK
)

:: ─── 3/7 Criar virtualenv ─────────────────────────────────────────────────
echo [3/7] Criando ambiente virtual Python...
if not exist "%INSTALL_DIR%\app\.venv\Scripts\python.exe" (
    python -m venv "%INSTALL_DIR%\app\.venv"
)
echo       OK

:: ─── 4/7 Instalar dependencias ────────────────────────────────────────────
echo [4/7] Instalando dependencias (aguarde 2-5 min)...
"%INSTALL_DIR%\app\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
:: Instala com --only-binary=:all: para pacotes dificeis de compilar
:: pywebview NAO esta no requirements.txt do servico (evita erro pythonnet)
"%INSTALL_DIR%\app\.venv\Scripts\pip.exe" install -r "%INSTALL_DIR%\app\requirements.txt" --quiet --no-warn-script-location
if %errorLevel% neq 0 (
    echo       [AVISO] Alguns pacotes opcionals falharam — continuando...
)
echo       OK

:: ─── 5/7 Instalar Playwright Chromium ────────────────────────────────────
echo [5/7] Instalando navegador WhatsApp (aguarde 5-10 min)...
set PLAYWRIGHT_BROWSERS_PATH=%INSTALL_DIR%\playwright-browsers
"%INSTALL_DIR%\app\.venv\Scripts\python.exe" -m playwright install chromium >"%INSTALL_DIR%\logs\playwright.log" 2>&1
if %errorLevel% neq 0 (
    echo       [AVISO] Playwright: veja %INSTALL_DIR%\logs\playwright.log
) else (
    echo       OK
)

:: ─── 6/7 Instalar NSSM ───────────────────────────────────────────────────
echo [6/7] Configurando servicos Windows...
if not exist "%NSSM_EXE%" (
    echo       Instalando NSSM via Chocolatey...
    :: Instala Chocolatey se nao existir
    where choco >nul 2>&1
    if %errorLevel% neq 0 (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
        :: Recarregar PATH para choco aparecer
        set "PATH=%PATH%;C:\ProgramData\chocolatey\bin"
    )
    choco install nssm --yes --no-progress --ignore-checksums >nul 2>&1

    :: Tentar copiar de locais conhecidos
    if exist "C:\ProgramData\chocolatey\bin\nssm.exe" (
        copy /Y "C:\ProgramData\chocolatey\bin\nssm.exe" "%NSSM_EXE%" >nul
        echo       NSSM instalado via Chocolatey.
    ) else if exist "C:\tools\nssm\nssm.exe" (
        copy /Y "C:\tools\nssm\nssm.exe" "%NSSM_EXE%" >nul
        echo       NSSM copiado de C:\tools\nssm.
    ) else (
        :: Fallback: baixar nssm diretamente
        echo       Tentando baixar NSSM diretamente...
        powershell -Command "try { Invoke-WebRequest 'https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip' -OutFile '%TEMP%\nssm.zip' -UseBasicParsing -TimeoutSec 30; Expand-Archive '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force; Copy-Item '%TEMP%\nssm\nssm-*\win64\nssm.exe' '%NSSM_EXE%' -Force } catch { Write-Host 'NSSM download falhou' }"
        del "%TEMP%\nssm.zip" >nul 2>&1
    )
)

if not exist "%NSSM_EXE%" (
    echo.
    echo  [ERRO] NSSM nao foi instalado. Os servicos nao serao registrados.
    echo  Voce pode iniciar manualmente com: %INSTALL_DIR%\tools\start_app.bat
    goto :criar_env
)
echo       NSSM OK

:: ─── Criar .env ──────────────────────────────────────────────────────────
:criar_env
if not exist "%INSTALL_DIR%\app\.env" (
    echo APP_STATE=locked> "%INSTALL_DIR%\app\.env"
    echo PORT=4000>> "%INSTALL_DIR%\app\.env"
    echo DATABASE_URL=data\app.db>> "%INSTALL_DIR%\app\.env"
    echo SECRET_KEY=>> "%INSTALL_DIR%\app\.env"
    echo MONITOR_URL=%MONITOR_URL%>> "%INSTALL_DIR%\app\.env"
    echo MONITOR_CLIENT_TOKEN=>> "%INSTALL_DIR%\app\.env"
    echo CLIENT_NAME=>> "%INSTALL_DIR%\app\.env"
    echo PLAYWRIGHT_BROWSERS_PATH=%INSTALL_DIR%\playwright-browsers>> "%INSTALL_DIR%\app\.env"
)

:: ─── Criar scripts de inicio ─────────────────────────────────────────────
(
echo @echo off
echo cd /d "%INSTALL_DIR%"
echo set PLAYWRIGHT_BROWSERS_PATH=%INSTALL_DIR%\playwright-browsers
echo "%INSTALL_DIR%\app\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 4000
) > "%INSTALL_DIR%\tools\start_app.bat"

(
echo @echo off
echo cd /d "%INSTALL_DIR%"
echo set PLAYWRIGHT_BROWSERS_PATH=%INSTALL_DIR%\playwright-browsers
echo "%INSTALL_DIR%\app\.venv\Scripts\python.exe" app\worker_main.py
) > "%INSTALL_DIR%\tools\start_worker.bat"

:: ─── Registrar servicos Windows ──────────────────────────────────────────
if not exist "%NSSM_EXE%" goto :iniciar

"%NSSM_EXE%" stop ZapDinApp    >nul 2>&1
"%NSSM_EXE%" stop ZapDinWorker >nul 2>&1
timeout /t 2 /nobreak >nul
"%NSSM_EXE%" remove ZapDinApp    confirm >nul 2>&1
"%NSSM_EXE%" remove ZapDinWorker confirm >nul 2>&1

"%NSSM_EXE%" install ZapDinApp "%INSTALL_DIR%\tools\start_app.bat"
"%NSSM_EXE%" set ZapDinApp AppDirectory "%INSTALL_DIR%"
"%NSSM_EXE%" set ZapDinApp DisplayName "ZapDin — Backend API"
"%NSSM_EXE%" set ZapDinApp Start SERVICE_AUTO_START
"%NSSM_EXE%" set ZapDinApp AppStdout "%INSTALL_DIR%\logs\app.log"
"%NSSM_EXE%" set ZapDinApp AppStderr "%INSTALL_DIR%\logs\app_err.log"
"%NSSM_EXE%" set ZapDinApp AppExit Default Restart
"%NSSM_EXE%" set ZapDinApp AppRestartDelay 5000

"%NSSM_EXE%" install ZapDinWorker "%INSTALL_DIR%\tools\start_worker.bat"
"%NSSM_EXE%" set ZapDinWorker AppDirectory "%INSTALL_DIR%"
"%NSSM_EXE%" set ZapDinWorker DisplayName "ZapDin — Worker"
"%NSSM_EXE%" set ZapDinWorker Start SERVICE_AUTO_START
"%NSSM_EXE%" set ZapDinWorker DependOnService ZapDinApp
"%NSSM_EXE%" set ZapDinWorker AppExit Default Restart
"%NSSM_EXE%" set ZapDinWorker AppRestartDelay 8000

:: ─── Liberar porta no firewall ───────────────────────────────────────────
netsh advfirewall firewall delete rule name="ZapDin App" >nul 2>&1
netsh advfirewall firewall add rule name="ZapDin App" dir=in action=allow protocol=TCP localport=4000 >nul

:: ─── 7/7 Iniciar servico ─────────────────────────────────────────────────
:iniciar
echo [7/7] Iniciando ZapDin...
if exist "%NSSM_EXE%" (
    "%NSSM_EXE%" start ZapDinApp
) else (
    echo       Iniciando em segundo plano (sem NSSM)...
    start "ZapDin App" /B "%INSTALL_DIR%\tools\start_app.bat"
)
timeout /t 5 /nobreak >nul

echo.
echo  =============================================
echo   ZapDin instalado com sucesso!
echo  =============================================
echo.
echo  Acesse: http://localhost:4000
echo.
echo  Proximos passos:
echo  1. No Monitor, gere um Token de Ativacao para este cliente
echo  2. Acesse http://localhost:4000
echo  3. Digite o token na tela de ativacao
echo.
echo  Logs em: %INSTALL_DIR%\logs\
echo.
pause
