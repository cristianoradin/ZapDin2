; =============================================================================
;  ZapDin App — Instalador Inteligente v2
;  Inno Setup 6.2+  |  Velopack 0.0.x  |  WebView2 Runtime  |  NSSM 2.24
;
;  Pipeline esperado antes de compilar este .iss:
;    1.  pyinstaller --onefile --noconsole launcher.spec      → ZapDin-App.exe
;    2.  pyinstaller --onefile --noconsole worker.spec        → ZapDin-Worker.exe
;    3.  playwright install chromium --with-deps --dry-run    → playwright-browsers/
;    4.  vpk pack --packId ZapDin --packVersion {{v}}         → vpk-output/
;       --packDir build/ --mainExe ZapDin-App.exe
;       --releaseNotes RELEASES.md
;
;  O pacote vpk gera Setup.exe + RELEASES + nupkg em vpk-output/.
;  Este .iss bootsrappea os pré-requisitos, copia o conteúdo do nupkg
;  para Program Files e entrega o controle ao Velopack.
; =============================================================================

#define AppName            "ZapDin"
#define AppVersion         "2.0.0"
#define AppPublisher       "ZapDin"
#define AppURL             "https://zapdin.com.br"
#define AppInstDir         "ZapDin"
#define ServiceApp         "ZapDinApp"
#define ServiceWorker      "ZapDinWorker"
#define AppPort            "4000"
#define UpdateChannelURL   "https://releases.zapdin.com.br/whatsapp"

; URLs de dependências (todas com fallback CDN oficial)
#define WebView2BootstrapURL   "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
#define VCRedist2022URL        "https://aka.ms/vs/17/release/vc_redist.x64.exe"
#define NSSMURL                "https://nssm.cc/release/nssm-2.24.zip"

[Setup]
AppId={{B5F2C9D1-7A4E-4F8B-9C12-2D5E8A1F3B47}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppInstDir}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputBaseFilename=ZapDin-Setup-{#AppVersion}
OutputDir=output
Compression=lzma2/ultra
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=payload\branding\zapdin.ico
UninstallDisplayIcon={app}\ZapDin-App.exe
CloseApplications=force
RestartApplications=no
MinVersion=10.0.17763   ; Windows 10 1809+

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; \
  GroupDescription: "Atalhos:"; Flags: checkedonce
Name: "autostart";   Description: "Iniciar serviços automaticamente com o Windows"; \
  GroupDescription: "Inicialização:"; Flags: checkedonce

[Files]
; ───── Executáveis empacotados pelo PyInstaller ───────────────────────────────
Source: "payload\ZapDin-App.exe";     DestDir: "{app}"; Flags: ignoreversion
Source: "payload\ZapDin-Worker.exe";  DestDir: "{app}"; Flags: ignoreversion

; ───── Velopack runtime (gerado por `vpk pack`) ───────────────────────────────
Source: "payload\velopack\Update.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "payload\velopack\*";          DestDir: "{app}\velopack"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ───── Playwright Chromium pré-baixado (~170 MB) ──────────────────────────────
Source: "payload\playwright-browsers\*"; DestDir: "{app}\playwright-browsers"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ───── NSSM (gerenciador de serviços) ─────────────────────────────────────────
Source: "payload\tools\nssm.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

; ───── Frontend SPA + branding ────────────────────────────────────────────────
Source: "payload\static\*";   DestDir: "{app}\static";   Flags: recursesubdirs
Source: "payload\branding\*"; DestDir: "{app}\branding"; Flags: recursesubdirs

; ───── Templates de configuração ──────────────────────────────────────────────
Source: "payload\.env.template";          DestDir: "{app}"; Flags: ignoreversion
Source: "payload\velopack-config.json";   DestDir: "{app}"; Flags: ignoreversion

; ───── Bootstrappers de dependência (baixados pré-build pelo CI) ──────────────
Source: "payload\deps\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not WebView2Installed
Source: "payload\deps\vc_redist.x64.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not VCRedistInstalled

[Dirs]
Name: "{app}\data";  Permissions: users-modify
Name: "{app}\logs";  Permissions: users-modify
Name: "{app}\cache"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ZapDin-App.exe"; \
  IconFilename: "{app}\branding\zapdin.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\ZapDin-App.exe"; \
  IconFilename: "{app}\branding\zapdin.ico"; Tasks: desktopicon
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"

[Run]
; ── Pré-requisitos (silent, só executam se Check retornar True) ──────────────
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
  Parameters: "/silent /install"; \
  StatusMsg: "Instalando Microsoft Edge WebView2 Runtime..."; \
  Flags: waituntilterminated; \
  Check: not WebView2Installed

Filename: "{tmp}\vc_redist.x64.exe"; \
  Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Instalando Visual C++ 2015-2022 Redistributable..."; \
  Flags: waituntilterminated; \
  Check: not VCRedistInstalled

; ── Registro de serviços (depois das deps) ───────────────────────────────────
Filename: "{cmd}"; \
  Parameters: "/C ""{tmp}\register-services.bat"""; \
  StatusMsg: "Registrando serviços do Windows..."; \
  Flags: runhidden waituntilterminated

; ── Primeira execução (modo bloqueio, aguarda token) ─────────────────────────
Filename: "{app}\ZapDin-App.exe"; \
  Parameters: "--first-run"; \
  Description: "Abrir {#AppName} para ativação"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C ""{app}\tools\unregister-services.bat"""; \
  Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cache"
Type: files;          Name: "{app}\.env"

; =============================================================================
;  [Code] — Lógica de detecção, download e provisionamento
; =============================================================================
[Code]
const
  ; Registry keys oficiais para detecção de WebView2
  WEBVIEW2_HKLM_X64 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WEBVIEW2_HKLM     = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  ; VC++ 2015-2022 redistributable (x64)
  VCREDIST_KEY      = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';

var
  RegisterServicesBatchPath: string;

// ─────────────────────────────────────────────────────────────────────────────
//  Detecção de pré-requisitos
// ─────────────────────────────────────────────────────────────────────────────

function WebView2Installed: Boolean;
var
  Version: string;
begin
  Result :=
    RegQueryStringValue(HKLM, WEBVIEW2_HKLM_X64, 'pv', Version) or
    RegQueryStringValue(HKLM, WEBVIEW2_HKLM,     'pv', Version) or
    RegQueryStringValue(HKCU, WEBVIEW2_HKLM,     'pv', Version);
  if Result then
    Log('[ZapDin] WebView2 já instalado: versão ' + Version)
  else
    Log('[ZapDin] WebView2 ausente — será instalado.');
end;

function VCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, VCREDIST_KEY, 'Installed', Installed)
            and (Installed = 1);
  if Result then
    Log('[ZapDin] VC++ Redistributable já instalado.')
  else
    Log('[ZapDin] VC++ Redistributable ausente — será instalado.');
end;

// ─────────────────────────────────────────────────────────────────────────────
//  Geração dos batch files de registro/desregistro de serviço
// ─────────────────────────────────────────────────────────────────────────────

procedure WriteServiceBatches;
var
  RegBat, UnregBat, AppDir, EnvFile: string;
  Lines: TArrayOfString;
begin
  AppDir  := ExpandConstant('{app}');
  EnvFile := AppDir + '\.env';
  RegisterServicesBatchPath := ExpandConstant('{tmp}\register-services.bat');
  UnregBat := AppDir + '\tools\unregister-services.bat';

  // ── register-services.bat ─────────────────────────────────────────────────
  SetArrayLength(Lines, 0);
  Lines := [
    '@echo off',
    'setlocal',
    'set NSSM=' + AppDir + '\tools\nssm.exe',
    'set APPDIR=' + AppDir,
    '',
    ':: Para serviços antigos antes de reinstalar',
    '%NSSM% stop {#ServiceApp}    >nul 2>&1',
    '%NSSM% stop {#ServiceWorker} >nul 2>&1',
    '%NSSM% remove {#ServiceApp}    confirm >nul 2>&1',
    '%NSSM% remove {#ServiceWorker} confirm >nul 2>&1',
    '',
    ':: ── Backend FastAPI ─────────────────────────────────────────────────',
    '%NSSM% install {#ServiceApp} "%APPDIR%\ZapDin-App.exe" --service',
    '%NSSM% set {#ServiceApp} AppDirectory "%APPDIR%"',
    '%NSSM% set {#ServiceApp} DisplayName "ZapDin — Backend (porta {#AppPort})"',
    '%NSSM% set {#ServiceApp} Description "API FastAPI + Socket.IO do ZapDin."',
    '%NSSM% set {#ServiceApp} Start SERVICE_AUTO_START',
    '%NSSM% set {#ServiceApp} ObjectName LocalSystem',
    '%NSSM% set {#ServiceApp} AppStdout "%APPDIR%\logs\app.stdout.log"',
    '%NSSM% set {#ServiceApp} AppStderr "%APPDIR%\logs\app.stderr.log"',
    '%NSSM% set {#ServiceApp} AppRotateFiles 1',
    '%NSSM% set {#ServiceApp} AppRotateBytes 10485760',
    '%NSSM% set {#ServiceApp} AppExit Default Restart',
    '%NSSM% set {#ServiceApp} AppRestartDelay 5000',
    '',
    ':: ── Worker (queue de mensagens/arquivos) ──────────────────────────',
    '%NSSM% install {#ServiceWorker} "%APPDIR%\ZapDin-Worker.exe" --service',
    '%NSSM% set {#ServiceWorker} AppDirectory "%APPDIR%"',
    '%NSSM% set {#ServiceWorker} DisplayName "ZapDin — Worker (fila de envios)"',
    '%NSSM% set {#ServiceWorker} Description "Processa fila de mensagens WhatsApp com anti-ban."',
    '%NSSM% set {#ServiceWorker} Start SERVICE_AUTO_START',
    '%NSSM% set {#ServiceWorker} DependOnService {#ServiceApp}',
    '%NSSM% set {#ServiceWorker} ObjectName LocalSystem',
    '%NSSM% set {#ServiceWorker} AppStdout "%APPDIR%\logs\worker.stdout.log"',
    '%NSSM% set {#ServiceWorker} AppStderr "%APPDIR%\logs\worker.stderr.log"',
    '%NSSM% set {#ServiceWorker} AppExit Default Restart',
    '',
    ':: Inicia ambos',
    '%NSSM% start {#ServiceApp}',
    '%NSSM% start {#ServiceWorker}',
    '',
    'endlocal'
  ];
  SaveStringsToFile(RegisterServicesBatchPath, Lines, False);

  // ── unregister-services.bat (rodado pelo uninstaller) ─────────────────────
  SetArrayLength(Lines, 0);
  Lines := [
    '@echo off',
    'set NSSM=' + AppDir + '\tools\nssm.exe',
    '%NSSM% stop {#ServiceWorker} >nul 2>&1',
    '%NSSM% stop {#ServiceApp}    >nul 2>&1',
    '%NSSM% remove {#ServiceWorker} confirm >nul 2>&1',
    '%NSSM% remove {#ServiceApp}    confirm >nul 2>&1'
  ];
  SaveStringsToFile(UnregBat, Lines, False);
end;

// ─────────────────────────────────────────────────────────────────────────────
//  Provisionamento mínimo do .env (placeholders — token preenche o resto)
// ─────────────────────────────────────────────────────────────────────────────

procedure WriteBootstrapEnv;
var
  EnvLines: TArrayOfString;
  EnvFile: string;
begin
  EnvFile := ExpandConstant('{app}\.env');

  // Se já existe (reinstalação), preserva
  if FileExists(EnvFile) then
  begin
    Log('[ZapDin] .env existente preservado.');
    Exit;
  end;

  EnvLines := [
    '# ZapDin — gerado pelo instalador. Valores sensíveis serão preenchidos',
    '# pelo fluxo de ativação por token (modo de bloqueio).',
    'APP_STATE=locked',
    'APP_PORT={#AppPort}',
    'PLAYWRIGHT_BROWSERS_PATH=' + ExpandConstant('{app}\playwright-browsers'),
    'VELOPACK_CHANNEL_URL={#UpdateChannelURL}',
    'VELOPACK_UPDATE_EXE=' + ExpandConstant('{app}\Update.exe'),
    '',
    '# Preenchidos após validação do token de ativação:',
    'CLIENT_TOKEN=',
    'MONITOR_URL=',
    'CLIENT_NAME=',
    'CLIENT_CNPJ=',
    'ERP_TOKEN=',
    'SECRET_KEY=' // será gerada no first-run
  ];
  SaveStringsToFile(EnvFile, EnvLines, False);
  Log('[ZapDin] .env de bootstrap criado em ' + EnvFile);
end;

// ─────────────────────────────────────────────────────────────────────────────
//  Hooks de ciclo de vida do Inno Setup
// ─────────────────────────────────────────────────────────────────────────────

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteBootstrapEnv;
    WriteServiceBatches;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;

  // Sanity check de arquitetura
  if not IsX64Compatible then
  begin
    Result := 'Este instalador requer Windows 64-bit.';
    Exit;
  end;
end;

procedure InitializeWizard;
begin
  // Aqui daria pra adicionar uma página customizada perguntando o
  // CLIENT_TOKEN logo no instalador (em vez de no first-run do app).
  // Mantenho no app por padrão — UX mais consistente com Velopack updates.
end;
