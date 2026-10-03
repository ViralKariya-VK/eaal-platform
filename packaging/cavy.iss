; Inno Setup script: wraps the PyInstaller folder into one CAVY-Setup.exe.
; Built by scripts/build_release.py (Windows only).
#define AppVersion GetEnv("CAVY_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif

[Setup]
AppName=CAVY
AppVersion={#AppVersion}
AppPublisher=CAVY
DefaultDirName={autopf}\CAVY
DefaultGroupName=CAVY
OutputDir=..\dist
OutputBaseFilename=CAVY-{#AppVersion}-Windows-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\CAVY.exe
WizardStyle=modern

[Files]
Source: "..\dist\CAVY\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\CAVY"; Filename: "{app}\CAVY.exe"
Name: "{autodesktop}\CAVY"; Filename: "{app}\CAVY.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\CAVY.exe"; Description: "Start CAVY"; Flags: nowait postinstall skipifsilent
