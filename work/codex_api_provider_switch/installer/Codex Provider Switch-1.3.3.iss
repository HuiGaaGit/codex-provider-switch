#ifndef MyAppName
#define MyAppName "Codex Provider Switch"
#endif
#ifndef MyAppVersion
#define MyAppVersion "1.3.3"
#endif
#ifndef MyAppExeName
#define MyAppExeName "Codex Provider Switch.exe"
#endif
#ifndef MyAppId
#define MyAppId "{{752780BD-6C48-4826-B864-23163B28BCE7}"
#endif
#ifndef MySetupName
#define MySetupName "Codex Provider Switch-Setup-1.3.3"
#endif
#ifndef SourceExe
#define SourceExe "..\..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.3.3.exe"
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
; Keep the normal non-silent close/continue/cancel prompt, but force-close
; only this executable after the user confirms. Codex processes are excluded.
CloseApplications=force
CloseApplicationsFilter=Codex Provider Switch.exe
RestartApplications=no
VersionInfoVersion=1.3.3.0
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

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ExistingExe: String;
begin
  { Newer builds can leave the tray cleanly through the local IPC command.
    Older builds or a hung process are handled by CloseApplications=force. }
  ExistingExe := ExpandConstant('{app}\{#MyAppExeName}');
  if FileExists(ExistingExe) then
    Exec(ExistingExe, '--installer-shutdown', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
