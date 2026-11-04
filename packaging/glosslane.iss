; Glosslane installer for Inno Setup 6, built by packaging/build.py.
; Installs for the current user only (no admin prompt), into
; %LOCALAPPDATA%\Programs\Glosslane. Models download on first start.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E2B8F41-3C7A-4D19-9B5E-7A1C0D4F8E62}
AppName=Glosslane
AppVersion={#AppVersion}
AppVerName=Glosslane {#AppVersion}
AppPublisher=Niro54321
AppPublisherURL=https://github.com/Niro54321/Glosslane
AppSupportURL=https://github.com/Niro54321/Glosslane/issues
AppUpdatesURL=https://github.com/Niro54321/Glosslane/releases
DefaultDirName={autopf}\Glosslane
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Glosslane-Setup-{#AppVersion}
SetupIconFile=..\build\glosslane.ico
UninstallDisplayIcon={app}\Glosslane.exe
UninstallDisplayName=Glosslane
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start Glosslane when Windows starts (it waits in the tray)"; Flags: unchecked
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Glosslane\*"; DestDir: "{app}"; Excludes: "glosslane-selftest.exe"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Glosslane"; Filename: "{app}\Glosslane.exe"
Name: "{autodesktop}\Glosslane"; Filename: "{app}\Glosslane.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Glosslane"; ValueData: """{app}\Glosslane.exe"" --background"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Glosslane.exe"; Description: "Start Glosslane now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM Glosslane.exe /F"; Flags: runhidden; RunOnceId: "StopGlosslane"

[UninstallDelete]
; The downloaded models and GPU pack are large, so they go with the app.
Type: filesandordirs; Name: "{localappdata}\Glosslane"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { An older Glosslane may be running from the tray; close it so its files can be replaced. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM Glosslane.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
