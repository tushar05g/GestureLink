[Setup]
AppId={{A9A4A1C8-580D-4CDB-B5E3-4011E63CDBB1}
AppName=GestureLink
AppVersion=1.0
AppPublisher=GestureLink Team
AppPublisherURL=https://github.com/gesturelink
AppSupportURL=https://github.com/gesturelink
AppUpdatesURL=https://github.com/gesturelink
DefaultDirName={autopf}\GestureLink
DefaultGroupName=GestureLink
AllowNoIcons=yes
LicenseFile=..\BUILD_INSTRUCTIONS.md
OutputDir=..\release
OutputBaseFilename=GestureLink_Installer
SetupIconFile=..\logo.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full installation (Hub & Agent)"
Name: "hub"; Description: "Hub installation only"
Name: "agent"; Description: "Agent installation only"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "hub"; Description: "GestureLink Hub (Main Controller)"; Types: full hub custom
Name: "agent"; Description: "GestureLink Agent (Remote Controlled Node)"; Types: full agent custom

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\release\GestureLink_Hub.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: hub
Source: "..\release\GestureLink_Agent.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: agent

[Icons]
Name: "{group}\GestureLink Hub"; Filename: "{app}\GestureLink_Hub.exe"; Components: hub
Name: "{group}\GestureLink Agent"; Filename: "{app}\GestureLink_Agent.exe"; Components: agent
Name: "{group}\{cm:UninstallProgram,GestureLink}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GestureLink Hub"; Filename: "{app}\GestureLink_Hub.exe"; Tasks: desktopicon; Components: hub
Name: "{autodesktop}\GestureLink Agent"; Filename: "{app}\GestureLink_Agent.exe"; Tasks: desktopicon; Components: agent

[Run]
Filename: "{app}\GestureLink_Hub.exe"; Description: "Launch GestureLink Hub"; Flags: nowait postinstall skipifsilent; Components: hub
Filename: "{app}\GestureLink_Agent.exe"; Description: "Launch GestureLink Agent"; Flags: nowait postinstall skipifsilent unchecked; Components: agent
