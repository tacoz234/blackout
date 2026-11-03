; Inno Setup script for the Windows installer. Built by `build.ps1 -Installer`.
; Per-user install: no admin prompt, installs to %LOCALAPPDATA%\Programs\Blackout
; (the same place `build.ps1 -Install` uses, so the two upgrade each other cleanly).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0"
#endif

[Setup]
AppId={{20DF5383-230E-41EF-A8D1-82DE97B6F575}
AppName=Blackout
AppVersion={#AppVersion}
AppVerName=Blackout {#AppVersion}
VersionInfoVersion={#NumericVersion}
AppPublisher=Blackout
DefaultDirName={autopf}\Blackout
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Blackout-{#AppVersion}-windows-setup
SetupIconFile=build\Blackout.ico
UninstallDisplayIcon={app}\Blackout.exe
UninstallDisplayName=Blackout
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
MinVersion=10.0

[Tasks]
Name: startup; Description: "Start Blackout when I sign in"
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "Blackout.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Blackout"; Filename: "{app}\Blackout.exe"
Name: "{autodesktop}\Blackout"; Filename: "{app}\Blackout.exe"; Tasks: desktopicon

[Registry]
; Same value the app's own "Start with Windows" checkbox writes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Blackout"; \
    ValueData: """{app}\Blackout.exe"" --background"; Tasks: startup

[Run]
Filename: "{app}\Blackout.exe"; Parameters: "--background"; Description: "Start Blackout now"; \
    Flags: nowait postinstall skipifsilent
; The app's built-in updater runs this installer silently with /RELAUNCH=1 and expects Blackout back afterwards.
Filename: "{app}\Blackout.exe"; Parameters: "--background"; Flags: nowait; Check: IsUpdate

[Messages]
FinishedLabel=Blackout lives in the system tray (by the clock). Press Ctrl+Alt+B or click the tray icon to black out your screens, and Escape to come back. Right-click the tray icon for options.

[Code]
function IsUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

procedure CloseBlackout();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Blackout.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseBlackout();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    CloseBlackout();
    // Also removes the entry if it was turned on from the tray menu rather than the installer.
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Blackout');
  end;
end;
