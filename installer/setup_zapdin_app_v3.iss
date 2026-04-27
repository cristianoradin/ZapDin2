; =============================================================================
;  ZapDin — Instalador Inteligente v3
;  Inno Setup 6.3+ | NSSM 2.24 | WebView2 | Velopack
;  Dois serviços Windows: ZapDinApp (backend) + ZapDinWorker (fila)
;
;  ── Pipeline CI esperado (GitHub Actions) ────────────────────────────────────
;  1. nuitka --onefile --output-filename=ZapDin-App.exe  app/launcher_service.py
;  2. nuitka --onefile --output-filename=ZapDin-Worker.exe app/worker_main.py
;  3. nuitka --onefile --output-filename=ZapDin-Launcher.exe app/launcher_gui.py
;  4. playwright install chromium --with-deps
;     → resultado copiado para payload/playwright-browsers/
;  5. vpk pack --packId ZapDin --packVersion {VER}
;              --packDir payload/ --mainExe ZapDin-App.exe
;              --releaseNotes RELEASES.md
;     → gera payload/Update.exe + delta packages
;  6. Inno Setup compila este .iss → output/ZapDin-Setup-{VER}.exe
; =============================================================================

#define AppName           "ZapDin"
#define AppVersion        "2.0.0"
#define AppPublisher      "ZapDin Sistemas"
#define AppURL            "https://zapdin.com.br"
#define ServiceApp        "ZapDinApp"
#define ServiceWorker     "ZapDinWorker"
#define AppPort           "4000"
#define UpdateChannelURL  "https://github.com/cristianoradin/ZapDin2/releases/latest/download"
#define DefaultMonitorURL "http://zapdin.gruposgapetro.com.br:5000"

; ──── URLs de bootstrappers de dependência ────────────────────────────────────
#define WebView2BootstrapURL "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
#define VCRedistURL          "https://aka.ms/vs/17/release/vc_redist.x64.exe"

; =============================================================================
[Setup]
AppId={{B5F2C9D1-7A4E-4F8B-9C12-2D5E8A1F3B47}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/suporte
VersionInfoVersion={#AppVersion}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputBaseFilename=ZapDin-Setup-{#AppVersion}
OutputDir=output
SetupIconFile=payload\branding\zapdin.ico

Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763     ; Windows 10 1809 — mínimo para WebView2
WizardStyle=modern
CloseApplications=force
RestartApplications=no

; =============================================================================
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

; =============================================================================
[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; \
  GroupDescription: "Atalhos:"; Flags: checkedonce

; =============================================================================
[Files]
; ── Executáveis (compilados por Nuitka no CI) ─────────────────────────────────
Source: "payload\ZapDin-App.exe";      DestDir: "{app}"; Flags: ignoreversion
Source: "payload\ZapDin-Worker.exe";   DestDir: "{app}"; Flags: ignoreversion
Source: "payload\ZapDin-Launcher.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Velopack — runtime de atualização ────────────────────────────────────────
Source: "payload\Update.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── NSSM — gerenciador de serviços Windows ────────────────────────────────────
Source: "payload\tools\nssm.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

; ── Playwright Chromium (pré-baixado no CI) ───────────────────────────────────
Source: "payload\playwright-browsers\*"; DestDir: "{app}\playwright-browsers"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ── Frontend SPA ──────────────────────────────────────────────────────────────
Source: "payload\static\*"; DestDir: "{app}\static"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ── Branding / ícone ──────────────────────────────────────────────────────────
Source: "payload\branding\zapdin.ico"; DestDir: "{app}\branding"; Flags: ignoreversion

; ── Template de configuração (sem segredos) ───────────────────────────────────
Source: "payload\.env.template"; DestDir: "{app}"; DestName: ".env.template"; \
  Flags: ignoreversion

; ── Bootstrappers de deps (deletados após uso) ────────────────────────────────
Source: "payload\deps\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not IsWebView2Installed
Source: "payload\deps\vc_redist.x64.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not IsVCRedistInstalled

; =============================================================================
[Dirs]
Name: "{app}\data";  Permissions: authusers-modify
Name: "{app}\logs";  Permissions: authusers-modify
Name: "{app}\cache"; Permissions: authusers-modify
Name: "{app}\tools"

; =============================================================================
[Icons]
Name: "{group}\{#AppName}"; \
  Filename: "{app}\ZapDin-Launcher.exe"; \
  IconFilename: "{app}\branding\zapdin.ico"; \
  Comment: "Abrir ZapDin"
Name: "{userdesktop}\{#AppName}"; \
  Filename: "{app}\ZapDin-Launcher.exe"; \
  IconFilename: "{app}\branding\zapdin.ico"; \
  Tasks: desktopicon
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"

; =============================================================================
; [Run] — executado APÓS cópia, em ordem
; =============================================================================
[Run]
; ── 1. Pré-requisitos silenciosos (apenas se ausentes) ────────────────────────
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
  Parameters: "/silent /install"; \
  StatusMsg: "Instalando Microsoft Edge WebView2 Runtime…"; \
  Flags: waituntilterminated; \
  Check: not IsWebView2Installed

Filename: "{tmp}\vc_redist.x64.exe"; \
  Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Instalando Visual C++ 2022 Redistributable…"; \
  Flags: waituntilterminated; \
  Check: not IsVCRedistInstalled

; ── 2. Registro de serviços Windows via NSSM ─────────────────────────────────
Filename: "{cmd}"; \
  Parameters: "/C ""{app}\tools\register_services.bat"""; \
  StatusMsg: "Configurando serviços do Windows…"; \
  Flags: runhidden waituntilterminated

; ── 3. Abre janela de ativação (first-run, opcional no fim da instalação) ─────
Filename: "{app}\ZapDin-Launcher.exe"; \
  Description: "Ativar {#AppName} agora"; \
  Flags: nowait postinstall skipifsilent unchecked

; =============================================================================
; [UninstallRun] — antes da remoção dos arquivos
; =============================================================================
[UninstallRun]
Filename: "{cmd}"; \
  Parameters: "/C ""{app}\tools\unregister_services.bat"""; \
  Flags: runhidden waituntilterminated

; =============================================================================
[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cache"
Type: files;          Name: "{app}\.env"

; =============================================================================
;  [Code] — Pascal: detecção de deps + wizard page MONITOR_URL + batch files
; =============================================================================
[Code]

const
  WV2_KEY_WOW = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_KEY     = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  VCREDIST_KEY = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';

var
  MonitorPage: TWizardPage;
  MonitorUrlEdit: TEdit;
  MonitorUrlLabel: TLabel;
  MonitorUrlHint: TLabel;

// ── Detecção de WebView2 ──────────────────────────────────────────────────────
function IsWebView2Installed: Boolean;
var V: string;
begin
  Result := RegQueryStringValue(HKLM, WV2_KEY_WOW, 'pv', V) or
            RegQueryStringValue(HKLM, WV2_KEY,     'pv', V) or
            RegQueryStringValue(HKCU, WV2_KEY,     'pv', V);
  if Result then Log('[ZapDin] WebView2 OK: ' + V)
  else           Log('[ZapDin] WebView2 ausente — será instalado');
end;

// ── Detecção de VC++ Redistributable ─────────────────────────────────────────
function IsVCRedistInstalled: Boolean;
var Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, VCREDIST_KEY, 'Installed', Installed)
            and (Installed = 1);
  if not Result then Log('[ZapDin] VC++ Redist ausente — será instalado');
end;

// ── Cria página de configuração do Monitor ────────────────────────────────────
procedure CreateMonitorPage;
begin
  MonitorPage := CreateCustomPage(
    wpSelectDir,
    'Configuração do Servidor Monitor',
    'Informe o endereço do servidor ZapDin Monitor'
  );

  MonitorUrlLabel := TLabel.Create(MonitorPage);
  MonitorUrlLabel.Parent := MonitorPage.Surface;
  MonitorUrlLabel.Caption := 'Endereço do Monitor (URL do servidor central):';
  MonitorUrlLabel.Left := 0;
  MonitorUrlLabel.Top := 16;
  MonitorUrlLabel.AutoSize := True;

  MonitorUrlEdit := TEdit.Create(MonitorPage);
  MonitorUrlEdit.Parent := MonitorPage.Surface;
  MonitorUrlEdit.Left := 0;
  MonitorUrlEdit.Top := 36;
  MonitorUrlEdit.Width := MonitorPage.SurfaceWidth;
  MonitorUrlEdit.Text := '{#DefaultMonitorURL}';
  MonitorUrlEdit.Font.Size := 10;

  MonitorUrlHint := TLabel.Create(MonitorPage);
  MonitorUrlHint.Parent := MonitorPage.Surface;
  MonitorUrlHint.Caption :=
    'Exemplos:' + #13#10 +
    '  http://192.168.1.100:5000   (rede local)' + #13#10 +
    '  http://monitor.suaempresa.com.br:5000   (domínio)' + #13#10 +
    '' + #13#10 +
    'Obtenha este endereço com o suporte ZapDin antes de prosseguir.';
  MonitorUrlHint.Left := 0;
  MonitorUrlHint.Top := 70;
  MonitorUrlHint.AutoSize := True;
  MonitorUrlHint.Font.Color := $666666;
end;

// ── Inicialização do wizard ───────────────────────────────────────────────────
procedure InitializeWizard;
begin
  CreateMonitorPage;
end;

// ── Validação ao avançar de página ───────────────────────────────────────────
function NextButtonClick(CurPageID: Integer): Boolean;
var Url: string;
begin
  Result := True;
  if CurPageID = MonitorPage.ID then begin
    Url := Trim(MonitorUrlEdit.Text);
    if (Url = '') or (Url = 'http://') or (Url = 'https://') then begin
      MsgBox(
        'Por favor, informe o endereço do Monitor ZapDin.' + #13#10 +
        'Exemplo: http://192.168.1.100:5000',
        mbError, MB_OK
      );
      Result := False;
      Exit;
    end;
    // Adiciona barra final para consistência
    if Url[Length(Url)] = '/' then
      Url := Copy(Url, 1, Length(Url) - 1);
    MonitorUrlEdit.Text := Url;
    Log('[ZapDin] MONITOR_URL configurado: ' + Url);
  end;
end;

// ── Cria .env de bootstrap com MONITOR_URL preenchido ────────────────────────
procedure WriteBootstrapEnv;
var
  EnvFile, MonitorUrl: string;
  Lines: TArrayOfString;
begin
  EnvFile := ExpandConstant('{app}\.env');
  if FileExists(EnvFile) then begin
    Log('[ZapDin] .env existente preservado (reinstalação).');
    Exit;
  end;

  MonitorUrl := Trim(MonitorUrlEdit.Text);

  SetArrayLength(Lines, 18);
  Lines[0]  := '# ZapDin — Bootstrap gerado pelo instalador v3';
  Lines[1]  := '# Segredos serão preenchidos pelo fluxo de ativação (/activate).';
  Lines[2]  := '';
  Lines[3]  := 'APP_STATE=locked';
  Lines[4]  := 'PORT={#AppPort}';
  Lines[5]  := 'DATABASE_URL=data\app.db';
  Lines[6]  := '';
  Lines[7]  := '# Endereço do servidor Monitor (configurado na instalação)';
  Lines[8]  := 'MONITOR_URL=' + MonitorUrl;
  Lines[9]  := '';
  Lines[10] := '# Preenchidos automaticamente após ativação por token:';
  Lines[11] := 'MONITOR_CLIENT_TOKEN=';
  Lines[12] := 'CLIENT_NAME=';
  Lines[13] := 'CLIENT_CNPJ=';
  Lines[14] := 'ERP_TOKEN=';
  Lines[15] := 'SECRET_KEY=';
  Lines[16] := '';
  Lines[17] := '# Configurados pelo instalador:';
  SaveStringsToFile(EnvFile, Lines, False);

  // Append linhas extras (evita problema de índice)
  SaveStringToFile(EnvFile,
    'PLAYWRIGHT_BROWSERS_PATH=' + ExpandConstant('{app}') + '\playwright-browsers' + #13#10 +
    'VELOPACK_CHANNEL_URL={#UpdateChannelURL}' + #13#10 +
    'VELOPACK_UPDATE_EXE=' + ExpandConstant('{app}') + '\Update.exe' + #13#10,
    True
  );

  Log('[ZapDin] .env de bootstrap criado — MONITOR_URL=' + MonitorUrl);
end;

// ── Gera register_services.bat e unregister_services.bat ─────────────────────
procedure WriteServiceBatches;
var
  AppDir, RegBat, UnregBat: string;
  RegLines, UnregLines: TArrayOfString;
begin
  AppDir  := ExpandConstant('{app}');
  RegBat  := AppDir + '\tools\register_services.bat';
  UnregBat := AppDir + '\tools\unregister_services.bat';

  // ── register_services.bat ────────────────────────────────────────────────
  SetArrayLength(RegLines, 55);
  RegLines[0]  := '@echo off';
  RegLines[1]  := 'setlocal';
  RegLines[2]  := 'set NSSM="' + AppDir + '\tools\nssm.exe"';
  RegLines[3]  := 'set APPDIR=' + AppDir;
  RegLines[4]  := 'set LOG=%APPDIR%\logs\install.log';
  RegLines[5]  := '';
  RegLines[6]  := 'echo [%date% %time%] Registrando servicos ZapDin... >> "%LOG%"';
  RegLines[7]  := '';
  RegLines[8]  := ':: Para instâncias anteriores';
  RegLines[9]  := '%NSSM% stop {#ServiceApp}    >nul 2>&1';
  RegLines[10] := '%NSSM% stop {#ServiceWorker} >nul 2>&1';
  RegLines[11] := 'timeout /t 3 /nobreak >nul';
  RegLines[12] := '%NSSM% remove {#ServiceApp}    confirm >nul 2>&1';
  RegLines[13] := '%NSSM% remove {#ServiceWorker} confirm >nul 2>&1';
  RegLines[14] := '';
  RegLines[15] := ':: =========================================================';
  RegLines[16] := ':: Serviço 1 — Backend FastAPI (porta {#AppPort})';
  RegLines[17] := ':: =========================================================';
  RegLines[18] := '%NSSM% install {#ServiceApp} "%APPDIR%\ZapDin-App.exe"';
  RegLines[19] := '%NSSM% set {#ServiceApp} AppParameters --service';
  RegLines[20] := '%NSSM% set {#ServiceApp} AppDirectory "%APPDIR%"';
  RegLines[21] := '%NSSM% set {#ServiceApp} DisplayName "ZapDin — Backend"';
  RegLines[22] := '%NSSM% set {#ServiceApp} Description "API FastAPI + WhatsApp Web (porta {#AppPort})."';
  RegLines[23] := '%NSSM% set {#ServiceApp} Start SERVICE_AUTO_START';
  RegLines[24] := '%NSSM% set {#ServiceApp} ObjectName LocalSystem';
  RegLines[25] := '%NSSM% set {#ServiceApp} AppStdout "%APPDIR%\logs\app.stdout.log"';
  RegLines[26] := '%NSSM% set {#ServiceApp} AppStderr "%APPDIR%\logs\app.stderr.log"';
  RegLines[27] := '%NSSM% set {#ServiceApp} AppRotateFiles 1';
  RegLines[28] := '%NSSM% set {#ServiceApp} AppRotateBytes 10485760';
  RegLines[29] := '%NSSM% set {#ServiceApp} AppExit Default Restart';
  RegLines[30] := '%NSSM% set {#ServiceApp} AppRestartDelay 5000';
  RegLines[31] := '%NSSM% set {#ServiceApp} AppEnvironmentExtra PLAYWRIGHT_BROWSERS_PATH="%APPDIR%\playwright-browsers"';
  RegLines[32] := '';
  RegLines[33] := ':: =========================================================';
  RegLines[34] := ':: Serviço 2 — Worker (fila de envios com anti-ban)';
  RegLines[35] := ':: Depende do Backend (DependOnService)';
  RegLines[36] := ':: =========================================================';
  RegLines[37] := '%NSSM% install {#ServiceWorker} "%APPDIR%\ZapDin-Worker.exe"';
  RegLines[38] := '%NSSM% set {#ServiceWorker} AppDirectory "%APPDIR%"';
  RegLines[39] := '%NSSM% set {#ServiceWorker} DisplayName "ZapDin — Worker"';
  RegLines[40] := '%NSSM% set {#ServiceWorker} Description "Processa fila de mensagens WhatsApp."';
  RegLines[41] := '%NSSM% set {#ServiceWorker} Start SERVICE_AUTO_START';
  RegLines[42] := '%NSSM% set {#ServiceWorker} DependOnService {#ServiceApp}';
  RegLines[43] := '%NSSM% set {#ServiceWorker} ObjectName LocalSystem';
  RegLines[44] := '%NSSM% set {#ServiceWorker} AppStdout "%APPDIR%\logs\worker.stdout.log"';
  RegLines[45] := '%NSSM% set {#ServiceWorker} AppStderr "%APPDIR%\logs\worker.stderr.log"';
  RegLines[46] := '%NSSM% set {#ServiceWorker} AppExit Default Restart';
  RegLines[47] := '%NSSM% set {#ServiceWorker} AppRestartDelay 8000';
  RegLines[48] := '';
  RegLines[49] := ':: Inicia Backend (Worker sobe automaticamente via DependOnService)';
  RegLines[50] := '%NSSM% start {#ServiceApp}';
  RegLines[51] := '';
  RegLines[52] := 'echo [%date% %time%] Servicos registrados com sucesso. >> "%LOG%"';
  RegLines[53] := 'endlocal';
  RegLines[54] := '';
  SaveStringsToFile(RegBat, RegLines, False);

  // ── unregister_services.bat ──────────────────────────────────────────────
  SetArrayLength(UnregLines, 10);
  UnregLines[0] := '@echo off';
  UnregLines[1] := 'set NSSM="' + AppDir + '\tools\nssm.exe"';
  UnregLines[2] := 'echo [%date% %time%] Removendo servicos ZapDin... >> "' + AppDir + '\logs\install.log"';
  UnregLines[3] := '%NSSM% stop {#ServiceWorker} >nul 2>&1';
  UnregLines[4] := '%NSSM% stop {#ServiceApp}    >nul 2>&1';
  UnregLines[5] := 'timeout /t 3 /nobreak >nul';
  UnregLines[6] := '%NSSM% remove {#ServiceWorker} confirm >nul 2>&1';
  UnregLines[7] := '%NSSM% remove {#ServiceApp}    confirm >nul 2>&1';
  UnregLines[8] := 'echo [%date% %time%] Servicos removidos. >> "' + AppDir + '\logs\install.log"';
  UnregLines[9] := '';
  SaveStringsToFile(UnregBat, UnregLines, False);
end;

// ── Hooks de ciclo de vida ────────────────────────────────────────────────────
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then begin
    ForceDirectories(ExpandConstant('{app}\logs'));
    ForceDirectories(ExpandConstant('{app}\tools'));
    ForceDirectories(ExpandConstant('{app}\data'));
  end;
  if CurStep = ssPostInstall then begin
    WriteBootstrapEnv;
    WriteServiceBatches;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  if not IsX64Compatible then
    Result := 'ZapDin requer Windows 64-bit (x64 ou ARM64 compatível).';
end;
