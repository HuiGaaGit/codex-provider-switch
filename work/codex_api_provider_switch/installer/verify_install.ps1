param(
    [string]$SetupPath = ""
)

$ErrorActionPreference = "Stop"
$version = "1.2.27"
$exeName = "Codex Provider Switch.exe"
if (-not $SetupPath) {
    $SetupPath = Join-Path $PSScriptRoot "..\..\..\outputs\codex_api_provider_switch\Codex Provider Switch-Setup-$version.exe"
}
$resolvedSetup = (Resolve-Path -LiteralPath $SetupPath).Path
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) (
    "CodexProviderSwitch-InstallTest-" + [guid]::NewGuid().ToString("N")
)
$installedExe = Join-Path $testDirectory $exeName
$uninstaller = Join-Path $testDirectory "unins000.exe"
$installExit = -1
$versionExit = -1
$smokeExit = -1
$trayExit = -1
$uninstallExit = -1

try {
    $install = Start-Process -FilePath $resolvedSetup -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/DIR=$testDirectory"
    ) -Wait -PassThru
    $installExit = $install.ExitCode
    if ($installExit -ne 0 -or -not (Test-Path -LiteralPath $installedExe)) {
        throw "Installer validation failed with exit code $installExit."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $testDirectory "THIRD_PARTY_NOTICES.md"))) {
        throw "Third-party notices were not installed."
    }

    $versionProcess = Start-Process -FilePath $installedExe -ArgumentList "--version" -Wait -PassThru
    $versionExit = $versionProcess.ExitCode
    $smoke = Start-Process -FilePath $installedExe -ArgumentList "--smoke-test" -Wait -PassThru
    $smokeExit = $smoke.ExitCode
    $tray = Start-Process -FilePath $installedExe -ArgumentList "--tray-smoke-test" -Wait -PassThru
    $trayExit = $tray.ExitCode
    if (@($versionExit, $smokeExit, $trayExit).Where({ $_ -ne 0 }).Count -gt 0) {
        throw "An installed executable validation command failed."
    }
}
finally {
    if (Test-Path -LiteralPath $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART"
        ) -Wait -PassThru
        $uninstallExit = $uninstall.ExitCode
    }
}

Start-Sleep -Milliseconds 500
$remaining = Test-Path -LiteralPath $installedExe
if ($uninstallExit -ne 0 -or $remaining) {
    throw "Uninstall validation failed or left the installed executable behind."
}

[pscustomobject]@{
    InstallExit = $installExit
    VersionExit = $versionExit
    SmokeExit = $smokeExit
    TrayExit = $trayExit
    UninstallExit = $uninstallExit
    InstalledExeRemoved = -not $remaining
} | Format-List






