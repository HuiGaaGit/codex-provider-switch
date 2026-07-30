@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "MODE=%~1"
if /I "%MODE%"=="--stop-only" goto :STOP_ONLY
if /I "%MODE%"=="--start-only" goto :START_ONLY
if /I "%MODE%"=="--do-restart" goto :DO_RESTART
if /I "%MODE%"=="--dry-run" goto :DRY_RUN
echo [ERROR] Use --stop-only, --start-only, --do-restart, or --dry-run.
exit /b 2

:DO_RESTART
call :ResolvePackage || exit /b 1
call :StopApp || exit /b 1
call :StartApp || exit /b 1
echo [INFO] Codex restarted successfully.
exit /b 0

:STOP_ONLY
call :ResolvePackage || exit /b 1
call :StopApp || exit /b 1
echo [INFO] Codex stopped successfully.
exit /b 0

:START_ONLY
if not "%~2"=="" (
  set "PACKAGE_ROOT=%~2"
  set "PACKAGE_FAMILY=%~3"
  set "APP_ID=App"
) else (
  call :ResolvePackage || exit /b 1
)
if not defined PACKAGE_FAMILY (
  echo [ERROR] Missing Codex package family for startup.
  exit /b 1
)
call :StartApp || exit /b 1
echo [INFO] Codex started successfully.
exit /b 0

:DRY_RUN
call :ResolvePackage || exit /b 1
call :CollectAppPids
echo [INFO] Package root: %PACKAGE_ROOT%
echo [INFO] Package family: %PACKAGE_FAMILY%
echo [INFO] Matching Codex App PIDs: %APP_PIDS%
echo [INFO] Relaunch target: shell:AppsFolder\%PACKAGE_FAMILY%^^!%APP_ID%
exit /b 0

:StopApp
call :CollectAppPids
if defined APP_PIDS (
  echo [INFO] Stopping Codex App process trees: !APP_PIDS!
  for %%P in (!APP_PIDS!) do taskkill /PID %%P /T /F >nul 2>&1
  call :WaitForExit || (
    echo [ERROR] Codex App did not exit within 25 seconds.
    exit /b 1
  )
  echo [INFO] Codex App processes are fully stopped.
) else (
  echo [INFO] No running Codex App process was found.
)
exit /b 0

:StartApp
echo [INFO] Launching Codex through Windows AppsFolder...
setlocal DisableDelayedExpansion
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$target = 'shell:AppsFolder\' + $env:PACKAGE_FAMILY + '!' + $env:APP_ID; Start-Process explorer.exe -ArgumentList $target"
set "LAUNCH_EXIT=%ERRORLEVEL%"
endlocal & set "LAUNCH_EXIT=%LAUNCH_EXIT%"
if not "%LAUNCH_EXIT%"=="0" (
  echo [ERROR] Windows could not launch Codex.
  exit /b 1
)
call :WaitForStart || (
  echo [ERROR] Codex did not start within 25 seconds.
  exit /b 1
)
exit /b 0

:ResolvePackage
set "PACKAGE_ROOT="
set "PACKAGE_FAMILY="
set "APP_ID=App"
for /f "usebackq tokens=1,* delims==" %%A in (`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$exe = @(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | ForEach-Object { $_.Path } | Where-Object { $_ -like '*\OpenAI.Codex_*\app\ChatGPT.exe' } | Select-Object -First 1); if (-not $exe) { $root = Join-Path ${env:ProgramFiles} 'WindowsApps'; $exe = @(Get-ChildItem -Path $root -Filter ChatGPT.exe -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\OpenAI.Codex_*\app\ChatGPT.exe' } | Select-Object -ExpandProperty FullName -First 1) }; if (-not $exe) { exit 2 }; $packageRoot = Split-Path -Parent (Split-Path -Parent $exe[0]); $packageName = Split-Path -Leaf $packageRoot; if ($packageName -notmatch '^(?<name>.+?)_[^_]+_[^_]+__(?<publisher>.+)$') { exit 3 }; 'PACKAGE_ROOT=' + $packageRoot; 'PACKAGE_FAMILY=' + $Matches.name + '_' + $Matches.publisher"`) do set "%%A=%%B"
if not defined PACKAGE_ROOT (
  echo [ERROR] Could not locate the installed Codex desktop package.
  exit /b 1
)
if not defined PACKAGE_FAMILY (
  echo [ERROR] Could not derive the Codex package family.
  exit /b 1
)
exit /b 0

:CollectAppPids
set "APP_PIDS="
for /f "usebackq delims=" %%P in (`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PACKAGE_ROOT; @(Get-Process -Name ChatGPT,codex -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } | Select-Object -ExpandProperty Id -Unique)"`) do set "APP_PIDS=!APP_PIDS! %%P"
exit /b 0

:WaitForExit
for /l %%S in (1,1,50) do (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PACKAGE_ROOT; if (@(Get-Process -Name ChatGPT,codex -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) }).Count -eq 0) { exit 0 } else { exit 1 }" >nul 2>&1
  if not errorlevel 1 exit /b 0
  timeout /t 1 /nobreak >nul
)
exit /b 1

:WaitForStart
for /l %%S in (1,1,50) do (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PACKAGE_ROOT; if (@(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0) { exit 0 } else { exit 1 }" >nul 2>&1
  if not errorlevel 1 exit /b 0
  timeout /t 1 /nobreak >nul
)
exit /b 1
