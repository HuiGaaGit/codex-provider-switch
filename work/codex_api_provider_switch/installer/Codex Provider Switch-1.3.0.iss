#define MyAppName "Codex Provider Switch"
#define MyAppVersion "1.3.0"
#define MyAppExeName "Codex Provider Switch.exe"
#ifndef MyAppId
#define MyAppId "{{752780BD-6C48-4826-B864-23163B28BCE7}"
#endif
#ifndef MySetupName
#define MySetupName "Codex Provider Switch-Setup-1.3.0"
#endif
#ifndef SourceExe
#define SourceExe "..\..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.3.0.exe"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Codex Provider Switch
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\..\outputs\codex_api_provider_switch
OutputBaseFilename={#MySetupName}
SetupIconFile=..\assets\codex_api_provider_switch_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Omit CloseApplications so Inno Setup uses its interactive close prompt.
; The application handles Windows Restart Manager messages and exits cleanly
; after consent; unrelated Codex processes are untouched.
CloseApplicationsFilter=Codex Provider Switch.exe
RestartApplications=no
VersionInfoVersion=1.3.0.0
VersionInfoDescription=Codex Provider Switch installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked
Name: "startup"; Description: "登录 Windows 后在系统托盘启动后台监控"; GroupDescription: "后台监控："; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "..\assets\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName} 后台监控"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tray"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
