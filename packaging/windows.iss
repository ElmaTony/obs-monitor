; SPDX-License-Identifier: GPL-3.0-only
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef TargetArch
  #error TargetArch is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

[Setup]
AppId={{B2F60A20-208F-45DF-9441-2B48FDE360D4}
AppName=OBS Monitor
AppVersion={#AppVersion}
AppPublisher=obs-monitor contributors
AppPublisherURL=https://github.com/ElmaTony/obs-monitor
AppSupportURL=https://github.com/ElmaTony/obs-monitor/issues
AppUpdatesURL=https://github.com/ElmaTony/obs-monitor/releases
DefaultDirName={localappdata}\Programs\obs-monitor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\obs-monitor.exe
LicenseFile={#SourcePath}..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=obs-monitor-{#AppVersion}-windows-{#TargetArch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
#if TargetArch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OBS Monitor"; Filename: "{app}\obs-monitor.exe"; Check: not WizardNoIcons
Name: "{autodesktop}\OBS Monitor"; Filename: "{app}\obs-monitor.exe"; Tasks: desktopicon; Check: not WizardNoIcons

[Run]
Filename: "{app}\obs-monitor.exe"; Description: "Launch OBS Monitor"; Flags: nowait postinstall skipifsilent
