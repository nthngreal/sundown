; Windows installer for Sundown (Inno Setup 6).
; Build: ISCC /DAppVersion=0.2.0 /DSourceDir=..\dist\sundown packaging\sundown.iss
;
; Contract:
; - Per-user install, no UAC prompt: %LOCALAPPDATA%\Programs\Sundown.
; - Start menu shortcut always, desktop shortcut on request.
; - No downloads or bootstrap steps: the PyInstaller folder is the whole payload.
; - "Launch Sundown" on the last page only after a successful install.
; - Uninstall removes the install folder and shortcuts. Settings (language, chosen
;   action, qBittorrent address) live in HKCU\Software\sundown and are kept, so a
;   reinstall remembers them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\sundown"
#endif

[Setup]
AppId={{6E0A4C1B-7D52-4E5B-9B3F-5A1D2C8E9F47}
AppName=Sundown
AppVersion={#AppVersion}
AppVerName=Sundown {#AppVersion}
AppPublisher=Bayashem
AppPublisherURL=https://github.com/nthngreal/sundown
AppSupportURL=https://github.com/nthngreal/sundown/issues
DefaultDirName={autopf}\Sundown
DefaultGroupName=Sundown
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Sundown-Setup-{#AppVersion}
SetupIconFile=sundown.ico
UninstallDisplayIcon={app}\sundown.exe
UninstallDisplayName=Sundown
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
AppMutex=SundownSingleInstanceMutex

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Sundown"; Filename: "{app}\sundown.exe"
Name: "{autodesktop}\Sundown"; Filename: "{app}\sundown.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\sundown.exe"; Description: "{cm:LaunchProgram,Sundown}"; Flags: nowait postinstall skipifsilent
