; Build from repository root with: ISCC.exe installer\vxtension.iss
#define AppVersion "0.1.0"

[Setup]
AppId={{F7969703-EA49-4536-927D-1FE95BEF016B}
AppName=vxtension
AppVersion={#AppVersion}
AppPublisher=vxtension
DefaultDirName={localappdata}\Programs\vxtension
DefaultGroupName=vxtension
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=vxtension-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\vxtension.exe
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로 가기 만들기"; Flags: unchecked

[Files]
Source: "..\dist\vxtension\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\vxtension"; Filename: "{app}\vxtension.exe"
Name: "{autodesktop}\vxtension"; Filename: "{app}\vxtension.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vxtension.exe"; Description: "vxtension 실행"; Flags: nowait postinstall skipifsilent
