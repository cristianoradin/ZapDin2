; ZapDin App — Inno Setup Installer
; Installs the WhatsApp messaging app on Windows

#define AppName "ZapDin App"
#define AppVersion "1.0.0"
#define AppPublisher "ZapDin"
#define AppURL "https://github.com/cristianoradin/zapdin2"
#define AppInstDir "ZapDin-App"
#define ServiceName "ZapDinApp"
#define AppPort "4000"
#define AppZip "zapdin-app.zip"
#define PythonMSI "python-3.12.9-amd64.msi"
#define PythonURL "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.msi"
#define NSSMUrl "https://nssm.cc/release/nssm-2.24.zip"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppInstDir}
DefaultGroupName={#AppName}
OutputBaseFilename=setup_zapdin_app
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce

[Files]
; The zip is downloaded at runtime by the [Run] section
; Include NSSM bundled if available
; Source: "tools\nssm.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\abrir.vbs"; IconFilename: "{app}\static\logo\icon.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\abrir.vbs"; IconFilename: "{app}\static\logo\icon.ico"; Tasks: desktopicon
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\cmd.exe"; Parameters: "/C ""{tmp}\install.bat"""; StatusMsg: "Instalando {#AppName}..."; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "{sys}\cmd.exe"; Parameters: "/C ""{app}\uninstall.bat"""; Flags: runhidden waituntilterminated

[Code]
var
  LogFile: string;

procedure Log(Msg: string);
var
  Lines: TArrayOfString;
begin
  SetArrayLength(Lines, 1);
  Lines[0] := Msg;
  SaveStringsToFile(LogFile, Lines, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  InstDir, TmpDir, BatchContent: string;
begin
  if CurStep = ssInstall then begin
    InstDir := ExpandConstant('{app}');
    TmpDir  := ExpandConstant('{tmp}');
    LogFile := InstDir + '\logs\install.log';

    ForceDirectories(InstDir + '\logs');
    Log('[' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '] Iniciando instalacao de {#AppName}');

    BatchContent :=
      '@echo off' + #13#10 +
      'setlocal' + #13#10 +
      'set INSTDIR=' + InstDir + #13#10 +
      'set LOGFILE=' + LogFile + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Verificando Python... >> "%LOGFILE%"' + #13#10 +
      'python --version >nul 2>&1' + #13#10 +
      'if %errorlevel% neq 0 (' + #13#10 +
      '  echo [%date% %time%] Python nao encontrado, baixando... >> "%LOGFILE%"' + #13#10 +
      '  powershell -Command "Invoke-WebRequest -Uri ''{#PythonURL}'' -OutFile ''%TEMP%\python_setup.msi'' -UseBasicParsing"' + #13#10 +
      '  msiexec /i "%TEMP%\python_setup.msi" /quiet PrependPath=1 Include_pip=1 /log "%LOGFILE%"' + #13#10 +
      '  del "%TEMP%\python_setup.msi"' + #13#10 +
      ')' + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Baixando app... >> "%LOGFILE%"' + #13#10 +
      'powershell -Command "Invoke-WebRequest -Uri ''https://github.com/cristianoradin/zapdin2/releases/latest/download/{#AppZip}'' -OutFile ''%TEMP%\{#AppZip}'' -UseBasicParsing"' + #13#10 +
      'powershell -Command "Expand-Archive -Path ''%TEMP%\{#AppZip}'' -DestinationPath ''%INSTDIR%'' -Force"' + #13#10 +
      'del "%TEMP%\{#AppZip}"' + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Instalando dependencias... >> "%LOGFILE%"' + #13#10 +
      'pip install -r "%INSTDIR%\requirements.txt" --quiet >> "%LOGFILE%" 2>&1' + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Instalando Playwright... >> "%LOGFILE%"' + #13#10 +
      'python -m playwright install chromium >> "%LOGFILE%" 2>&1' + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Configurando servico Windows... >> "%LOGFILE%"' + #13#10 +
      'sc query {#ServiceName} >nul 2>&1' + #13#10 +
      'if %errorlevel% equ 0 sc stop {#ServiceName} >nul 2>&1' + #13#10 +
      '' + #13#10 +
      ':: Create .env if not present' + #13#10 +
      'if not exist "%INSTDIR%\.env" copy "%INSTDIR%\.env.example" "%INSTDIR%\.env" >nul' + #13#10 +
      '' + #13#10 +
      ':: Register Task Scheduler to run uvicorn on boot (no NSSM required)' + #13#10 +
      'schtasks /delete /tn "{#ServiceName}" /f >nul 2>&1' + #13#10 +
      'schtasks /create /tn "{#ServiceName}" /tr "python -m uvicorn app.main:app --host 0.0.0.0 --port {#AppPort}" /sc onstart /ru SYSTEM /rl HIGHEST /f >> "%LOGFILE%" 2>&1' + #13#10 +
      'schtasks /run /tn "{#ServiceName}" >> "%LOGFILE%" 2>&1' + #13#10 +
      '' + #13#10 +
      ':: VBS shortcut (opens browser without cmd window)' + #13#10 +
      'echo Set WshShell = CreateObject("WScript.Shell") > "%INSTDIR%\abrir.vbs"' + #13#10 +
      'echo WshShell.Run "http://localhost:{#AppPort}", 0, False >> "%INSTDIR%\abrir.vbs"' + #13#10 +
      '' + #13#10 +
      'echo [%date% %time%] Instalacao concluida! >> "%LOGFILE%"' + #13#10 +
      'endlocal';

    SaveStringToFile(TmpDir + '\install.bat', BatchContent, False);
  end;
end;
