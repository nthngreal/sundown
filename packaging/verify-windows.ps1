# Release checks for the Windows installer, run by CI on a clean runner.
# Usage: pwsh packaging/verify-windows.ps1 -Setup dist\Sundown-Setup-X.exe
# Exits non-zero on the first failed check. Every check prints one "OK"/"FAIL" line.
param([Parameter(Mandatory = $true)][string]$Setup)

$ErrorActionPreference = "Stop"
$Setup = (Resolve-Path $Setup).Path
$AppDir = Join-Path $env:LOCALAPPDATA "Programs\Sundown"
$Exe = Join-Path $AppDir "sundown.exe"
$StartLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Sundown.lnk"
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E0A4C1B-7D52-4E5B-9B3F-5A1D2C8E9F47}_is1"
$Work = Join-Path $env:RUNNER_TEMP "sundown-verify"
New-Item -ItemType Directory -Force $Work | Out-Null

function Check($ok, $what) {
    if ($ok) { Write-Host "OK   $what" } else { Write-Host "FAIL $what"; exit 1 }
}

function Install($log, $extra = @()) {
    $argList = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$log`"") + $extra
    $p = Start-Process -FilePath $Setup -ArgumentList $argList -Wait -PassThru
    return $p.ExitCode
}

function Uninstall() {
    $u = Join-Path $AppDir "unins000.exe"
    $p = Start-Process -FilePath $u -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
    # The uninstaller hands off to a copy of itself in TEMP; give it time to finish.
    for ($i = 0; $i -lt 30 -and (Test-Path $Exe); $i++) { Start-Sleep -Seconds 1 }
    return $p.ExitCode
}

function SelfTest($name) {
    $out = Join-Path $Work "$name.json"
    $p = Start-Process -FilePath $Exe -ArgumentList "--selftest", "`"$out`"" -Wait -PassThru
    if (Test-Path $out) { Get-Content $out | Write-Host }
    return ($p.ExitCode -eq 0) -and (Test-Path $out) -and ((Get-Content $out -Raw | ConvertFrom-Json).ok)
}

Write-Host "== artifact"
$item = Get-Item $Setup
$hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLower()
Write-Host "$($item.Name)  $($item.Length) bytes  sha256 $hash"

Write-Host "== install (clean runner, standard per-user install, no UAC)"
Check ((Install (Join-Path $Work "install1.log")) -eq 0) "installer exit code 0"
Check (Test-Path $Exe) "sundown.exe in $AppDir"
Check (Test-Path $StartLnk) "Start menu shortcut"
Check (Test-Path $UninstKey) "Apps & features entry"

Write-Host "== first run and second run, from the installed copy"
Check (SelfTest "run1") "first run self-test"
Check (SelfTest "run2") "second run self-test"

Write-Host "== uninstall"
Check ((Uninstall) -eq 0) "uninstaller exit code 0"
Check (-not (Test-Path $Exe)) "program files removed"
Check (-not (Test-Path $StartLnk)) "Start menu shortcut removed"
Check (-not (Test-Path $UninstKey)) "Apps & features entry removed"

Write-Host "== reinstall"
Check ((Install (Join-Path $Work "install2.log")) -eq 0) "reinstall exit code 0"
Check (SelfTest "run3") "self-test after reinstall"
Check ((Uninstall) -eq 0) "second uninstall exit code 0"

Write-Host "== forced failure: target folder cannot be written"
$bad = Join-Path $Work "locked"
New-Item -ItemType Directory -Force $bad | Out-Null
$lockFile = Join-Path $bad "sundown.exe"
Set-Content $lockFile "placeholder"
$lock = [System.IO.File]::Open($lockFile, "Open", "ReadWrite", "None")
try {
    # /NOCLOSEAPPLICATIONS: the lock is held by this very shell, which must survive.
    $code = Install (Join-Path $Work "install-fail.log") @("/DIR=`"$bad`"", "/NOCLOSEAPPLICATIONS")
} finally {
    $lock.Close()
}
Write-Host "failure run exit code: $code"
Check ($code -ne 0) "installer reports failure (non-zero exit)"
Check (-not (Test-Path $StartLnk)) "no Start menu shortcut after a failed install"
Check (-not (Get-Process sundown -ErrorAction SilentlyContinue)) "app not started after a failed install"
Get-Process sundown -ErrorAction SilentlyContinue | Stop-Process -Force
$failUninst = Join-Path $bad "unins000.exe"
if (Test-Path $failUninst) {
    Start-Process $failUninst -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES" -Wait | Out-Null
}

Write-Host "== all checks passed for $($item.Name) sha256 $hash"
