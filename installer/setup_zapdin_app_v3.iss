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

#include "version.iss"
#define AppName           "ZapDin"
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
OutputDir=..\output
SetupIconFile=..\payload\branding\zapdin.ico

Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
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
Source: "..\payload\ZapDin-App.exe";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\payload\ZapDin-Worker.exe";   DestDir: "{app}"; Flags: ignoreversion
Source: "..\payload\ZapDin-Launcher.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Velopack — runtime de atualização ────────────────────────────────────────
Source: "..\payload\Update.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Playwright Chromium (pré-baixado no CI) ───────────────────────────────────
Source: "..\payload\playwright-browsers\*"; DestDir: "{app}\playwright-browsers"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ── Frontend SPA ──────────────────────────────────────────────────────────────
Source: "..\payload\static\*"; DestDir: "{app}\static"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ── Branding / ícone ──────────────────────────────────────────────────────────
Source: "..\payload\branding\zapdin.ico"; DestDir: "{app}\branding"; Flags: ignoreversion

; ── Template de configuração (sem segredos) ───────────────────────────────────
Source: "..\payload\.env.template"; DestDir: "{app}"; DestName: ".env.template"; \
  Flags: ignoreversion

; ── Bootstrappers de deps (deletados após uso) ────────────────────────────────
Source: "..\payload\deps\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not IsWebView2Installed
Source: "..\payload\deps\vc_redist.x64.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Check: not IsVCRedistInstalled

; =============================================================================
[Dirs]
Name: "{app}\data";  Permissions: authusers-modify
Name: "{app}\logs";  Permissions: authusers-modify
Name: "{app}\cache"; Permissions: authusers-modify

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

; ── 2. Registrar tarefas agendadas via PowerShell (sem NSSM) ─────────────────
Filename: "powershell.exe"; \
  Parameters: "-ExecutionPolicy Bypass -File ""{app}\tools\register_tasks.ps1"""; \
  StatusMsg: "Configurando inicializacao automatica..."; \
  Flags: runhidden waituntilterminated

; ── 3. Iniciar o app imediatamente ───────────────────────────────────────────
Filename: "schtasks.exe"; \
  Parameters: "/run /tn ZapDinApp"; \
  StatusMsg: "Iniciando ZapDin..."; \
  Flags: runhidden waituntilterminated

; ── 4. Abre janela de ativacao (first-run, opcional no fim da instalacao) ────

Filename: "{app}\ZapDin-Launcher.exe"; \
  Description: "Ativar {#AppName} agora"; \
  Flags: nowait postinstall skipifsilent unchecked

; =============================================================================
; [UninstallRun] — antes da remoção dos arquivos
; =============================================================================
[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/end /tn ZapDinApp";    Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/end /tn ZapDinWorker"; Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/delete /tn ZapDinApp    /f"; Flags: runhidden waituntilterminated
Filename: "schtasks.exe"; Parameters: "/delete /tn ZapDinWorker /f"; Flags: runhidden waituntilterminated
Filename: "taskkill.exe"; Parameters: "/IM ZapDin-App.exe /F";    Flags: runhidden waituntilterminated
Filename: "taskkill.exe"; Parameters: "/IM ZapDin-Worker.exe /F"; Flags: runhidden waituntilterminated

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

// ── Gera SECRET_KEY aleatoria (48 chars alfanumericos) ───────────────────────
function GenerateSecretKey: string;
var
  i, idx: Integer;
  chars: string;
  key: string;
  seed: Cardinal;
begin
  chars := 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  key   := '';
  seed  := GetTickCount;
  for i := 1 to 48 do
  begin
    seed := seed * 1664525 + 1013904223;
    idx  := (seed mod 62) + 1;
    key  := key + chars[idx];
  end;
  Result := key;
end;

// ── Cria .env de bootstrap com MONITOR_URL e SECRET_KEY ──────────────────────
procedure WriteBootstrapEnv;
var
  EnvFile, AppDir, MonitorUrl, SecretKey: string;
begin
  EnvFile    := ExpandConstant('{app}\.env');
  AppDir     := ExpandConstant('{app}');
  MonitorUrl := Trim(MonitorUrlEdit.Text);
  SecretKey  := GenerateSecretKey;

  if FileExists(EnvFile) then begin
    Log('[ZapDin] .env existente preservado (reinstalacao).');
    Exit;
  end;

  SaveStringToFile(EnvFile,
    'APP_STATE=locked'                                              + #13#10 +
    'PORT={#AppPort}'                                              + #13#10 +
    'DATABASE_URL=' + AppDir + '\data\app.db'                     + #13#10 +
    'SECRET_KEY=' + SecretKey                                      + #13#10 +
    'MONITOR_URL=' + MonitorUrl                                    + #13#10 +
    'MONITOR_CLIENT_TOKEN='                                        + #13#10 +
    'CLIENT_NAME='                                                 + #13#10 +
    'CLIENT_CNPJ='                                                 + #13#10 +
    'ERP_TOKEN='                                                   + #13#10 +
    'PLAYWRIGHT_BROWSERS_PATH=' + AppDir + '\playwright-browsers'  + #13#10 +
    'VELOPACK_CHANNEL_URL={#UpdateChannelURL}'                     + #13#10 +
    'VELOPACK_UPDATE_EXE=' + AppDir + '\Update.exe'               + #13#10,
    False
  );

  Log('[ZapDin] .env criado com SECRET_KEY e MONITOR_URL=' + MonitorUrl);
end;

// ── Gera register_tasks.ps1 (Task Scheduler, sem NSSM) ───────────────────────
procedure WriteRegisterTasksScript;
var
  AppDir, ScriptFile: string;
begin
  AppDir     := ExpandConstant('{app}');
  ScriptFile := AppDir + '\tools\register_tasks.ps1';

  ForceDirectories(AppDir + '\tools');

  SaveStringToFile(ScriptFile,
    '$app = "' + AppDir + '"'                                                          + #13#10 +
    '$log = "$app\logs\install.log"'                                                   + #13#10 +
    'Add-Content $log ("[$(Get-Date)] Registrando tarefas ZapDin...")'                + #13#10 +
    ''                                                                                 + #13#10 +
    '# Limpar tarefas anteriores'                                                      + #13#10 +
    'schtasks /end    /tn "ZapDinApp"    >$null 2>&1'                                 + #13#10 +
    'schtasks /end    /tn "ZapDinWorker" >$null 2>&1'                                 + #13#10 +
    'schtasks /delete /tn "ZapDinApp"    /f >$null 2>&1'                             + #13#10 +
    'schtasks /delete /tn "ZapDinWorker" /f >$null 2>&1'                             + #13#10 +
    ''                                                                                 + #13#10 +
    '# Registrar ZapDinApp — inicia no boot como SYSTEM'                              + #13#10 +
    'schtasks /create /tn "ZapDinApp" /tr "`"$app\ZapDin-App.exe`"" /sc onstart /ru SYSTEM /rl HIGHEST /f' + #13#10 +
    ''                                                                                 + #13#10 +
    '# Registrar ZapDinWorker — inicia 20s apos o boot'                              + #13#10 +
    'schtasks /create /tn "ZapDinWorker" /tr "`"$app\ZapDin-Worker.exe`"" /sc onstart /ru SYSTEM /rl HIGHEST /delay 0000:20 /f' + #13#10 +
    ''                                                                                 + #13#10 +
    'Add-Content $log ("[$(Get-Date)] Tarefas registradas com sucesso.")'             + #13#10,
    False
  );
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
    WriteRegisterTasksScript;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  if not IsX64Compatible then
    Result := 'ZapDin requer Windows 64-bit (x64 ou ARM64 compatível).';
end;
