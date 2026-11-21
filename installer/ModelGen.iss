; ModelGen's setup (Inno Setup 6). Built by installer\Build-Installer.bat, which fills the folder
; StageDir with exactly what ships and passes the version in.
;
; The setup copies ModelGen into the user's own programs folder (no admin rights needed), then runs
; install-deps.ps1, which checks the PC and installs only what is missing (ClaudeIn from its GitHub
; releases, Python 3.14, ModelGen's packages, Blender 5.2 or newer). Running a newer setup over an
; older install updates it and keeps the person's models (projects), parts library and settings (data).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\dist\stage"
#endif
; The GitHub repository whose latest release carries ClaudeIn-Setup-<version>.exe; a PC without
; ClaudeIn gets it from there.
#define ClaudeInRepo "ikverse/ClaudeIn"

[Setup]
AppId={{8F3C2A51-6B0D-4E4B-9C61-2D7A1E5B9F40}
AppName=ModelGen
AppVersion={#AppVersion}
AppVerName=ModelGen {#AppVersion}
AppPublisher=ModelGen
AppPublisherURL=https://github.com/ikverse/modelgen
AppSupportURL=https://github.com/ikverse/modelgen/issues
AppUpdatesURL=https://github.com/ikverse/modelgen/releases
DefaultDirName={localappdata}\Programs\ModelGen
DisableDirPage=auto
DefaultGroupName=ModelGen
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ModelGen-Setup
SetupIconFile={#StageDir}\assets\modelgen.ico
UninstallDisplayIcon={app}\assets\modelgen.ico
UninstallDisplayName=ModelGen
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Messages]
FinishedLabel=ModelGen is installed. Start it from the Start menu or the desktop; its page opens in your browser.%n%nModelGen reaches Claude through ClaudeIn, so keep ClaudeIn running (its icon is in the hidden icons by the clock). If ClaudeIn asks whether ModelGen may use a model, allow it.

[Tasks]
Name: "desktopicon"; Description: "Add a ModelGen shortcut to the desktop"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; ModelGen.vbs starts ModelGen.bat with no cmd window; the server stops a little after its page closes
Name: "{autoprograms}\ModelGen"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\ModelGen.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\modelgen.ico"
Name: "{autodesktop}\ModelGen"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\ModelGen.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\modelgen.ico"; Tasks: desktopicon

[Run]
; after a normal install: a "Start ModelGen" box on the last page
Filename: "{sys}\wscript.exe"; Parameters: """{app}\ModelGen.vbs"""; Description: "Start ModelGen"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent; Check: DepsReady
; after a quiet install (an update from inside ModelGen): start it again; the open page reconnects
Filename: "{sys}\wscript.exe"; Parameters: """{app}\ModelGen.vbs"" /noopen"; WorkingDir: "{app}"; Flags: nowait; Check: WizardSilent and DepsReady

[UninstallDelete]
; the program and what setup made for it; the person's own work (projects, library, data) stays
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\blender"
Type: filesandordirs; Name: "{app}\modelgen"
Type: files; Name: "{app}\installed.json"
Type: files; Name: "{app}\install.log"

[Code]
var
  DepsOk: Boolean;

function DepsReady: Boolean;
begin
  Result := DepsOk;
end;

{ Close a ModelGen that runs from this folder, so its files can be replaced. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
  Dir, Cmd: String;
begin
  Dir := WizardDirValue;
  StringChangeEx(Dir, '''', '''''', True);
  Cmd := '-NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq ''python.exe'' -and ' +
         '$_.CommandLine -like ''*-m modelgen.server*'' -and $_.ExecutablePath -like ''' + Dir + '\*'' } | ' +
         'ForEach-Object { taskkill /T /F /PID $_.ProcessId | Out-Null }"';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Cmd, '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := '';
end;

function LastLogLine: String;
var
  Lines: TArrayOfString;
begin
  Result := '';
  if LoadStringsFromFile(ExpandConstant('{app}\install.log'), Lines) and (GetArrayLength(Lines) > 0) then
    Result := Lines[GetArrayLength(Lines) - 1];
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RC, Show: Integer;
  Params: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  WizardForm.StatusLabel.Caption := 'Checking this PC and installing what is missing (ClaudeIn, Python, packages, Blender) ...';
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\install-deps.ps1') +
            '" -AppDir "' + ExpandConstant('{app}') + '" -Version "{#AppVersion}" -ClaudeInRepo "{#ClaudeInRepo}"';
  if WizardSilent then
    Show := SW_SHOWMINNOACTIVE
  else
    Show := SW_SHOW;
  DepsOk := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, ExpandConstant('{app}'),
                 Show, ewWaitUntilTerminated, RC) and (RC = 0);
  if (not DepsOk) and (not WizardSilent) then
    MsgBox('ModelGen was copied, but setting it up did not finish:' + #13#10#13#10 + LastLogLine + #13#10#13#10 +
           'The full report is in ' + ExpandConstant('{app}\install.log') + '. Fix that, then run this setup again.',
           mbError, MB_OK);
end;
