; glasswork.iss — the one-click Windows installer. Built by installer/build_windows.py,
; which gathers the program, its own Python and its own Node into SourceDir first.
;
; What the person sees: Welcome → the licence → where to install the program →
; where to keep their work → shortcuts → Install → "Open it now". No administrator
; password is needed: it installs for this Windows user only.
;
; Their work (accounts, websites, leads, settings) is kept in a separate folder, so
; updating or removing the program never touches it. Removing the program asks
; before deleting that folder, and the answer that is offered first is No.

#ifndef AppName
  #define AppName "Glasswork"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\stage\app"
#endif
#ifndef OutputDir
  #define OutputDir "..\build"
#endif
#ifndef OutputBase
  #define OutputBase "glasswork-setup"
#endif

[Setup]
; The same id for every version, so a new Setup.exe updates the installed one.
AppId={{6C1E8F2A-5B7D-4E3A-9F1C-2D8B7A4E6F01}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
; Not signed, by choice: no certificate subscription. Windows shows "Windows
; protected your PC" the first time; More info → Run anyway installs it. These
; lines at least give the file its own name and version in that box and in
; the file's Properties.
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourceDir}\installer\glasswork.ico
UninstallDisplayIcon={app}\installer\glasswork.ico
UninstallDisplayName={#AppName}
LicenseFile={#SourceDir}\installer\LICENSE.txt
; A running copy is closed for the update and nothing else on the computer is.
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put {#AppName} on the desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\glasswork.ico"; Comment: "Open {#AppName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\glasswork.ico"; Comment: "Open {#AppName}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launch.pyw"""; WorkingDir: "{app}"; Description: "Open {#AppName} now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\node_modules"
Type: filesandordirs; Name: "{app}\updates"
Type: files; Name: "{app}\install.json"

[Code]
var
  DataPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataPage := CreateInputDirPage(wpSelectDir,
    'Where should your work be kept?',
    'Your accounts, websites, leads, posts and settings live in this folder.',
    'They are kept here, apart from the program, so updating or removing the program never touches them.' + #13#10 +
    'Keep the suggested folder, or choose another, then click Next.',
    False, '');
  DataPage.Add('');
  DataPage.Values[0] := GetPreviousData('DataDir', ExpandConstant('{localappdata}\{#AppName}'));
  if ExpandConstant('{param:DATADIR|}') <> '' then
    DataPage.Values[0] := ExpandConstant('{param:DATADIR|}');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDir', DataPage.Values[0]);
end;

function JsonText(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(DataPage.Values[0]);
    SaveStringToFile(ExpandConstant('{app}\install.json'),
      '{"data": "' + JsonText(DataPage.Values[0]) + '", "version": "{#AppVersion}"}', False);
  end;
end;

function DataDirOf(App: String): String;
var
  Raw: AnsiString;
  S: String;
  P: Integer;
begin
  Result := '';
  if not LoadStringFromFile(App + '\install.json', Raw) then exit;
  S := String(Raw);
  P := Pos('"data": "', S);
  if P = 0 then exit;
  S := Copy(S, P + 9, Length(S));
  P := Pos('", "', S);
  if P = 0 then P := Pos('"}', S);
  if P = 0 then exit;
  S := Copy(S, 1, P - 1);
  StringChangeEx(S, '\\', '\', True);
  Result := S;
end;

var
  KeptData: String;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KeptData := DataDirOf(ExpandConstant('{app}'));
  if CurUninstallStep = usPostUninstall then
  begin
    if (KeptData <> '') and DirExists(KeptData) and not UninstallSilent then
      if MsgBox('Also delete your work — accounts, websites, leads and settings — in' + #13#10 + KeptData + '?' + #13#10#13#10 +
                'Choose No to keep it (installing the program again picks it up). This cannot be undone.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(KeptData, True, True, True);
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
