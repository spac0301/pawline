#define AppVersion "0.2.8"
[Setup]
AppId=Pawline
AppName=Pawline
AppVersion={#AppVersion}
AppPublisher=spac0301
AppPublisherURL=https://github.com/spac0301/pawline
AppSupportURL=https://github.com/spac0301/pawline/issues
DefaultDirName={localappdata}\Programs\Pawline
DefaultGroupName=Pawline
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\installer
OutputBaseFilename=Pawline-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\pawline\pawline.exe
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
InfoAfterFile=install-note.txt

[Files]
Source: "..\dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Pawline"; Filename: "{app}\pawline\pawline.exe"; AppUserModelID: "spac0301.Pawline"
Name: "{autoprograms}\Pawline - Help"; Filename: "https://github.com/spac0301/pawline/blob/main/WINDOWS.md"

[Run]
Filename: "{app}\pawline\pawline.exe"; Description: "Launch Pawline"; Flags: nowait postinstall skipifsilent unchecked
