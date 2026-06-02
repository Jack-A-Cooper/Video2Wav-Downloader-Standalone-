$ErrorActionPreference = "Stop"

# Build script for generated Windows executables.
#
# Video2WAV.exe is produced with PyInstaller because the main project is Python.
# uninstall.exe is produced with dotnet so it can run even when Python or the
# project virtual environment is missing/broken.

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Set-Location $ProjectRoot

function Write-CrashReport($Context, $ErrorRecord) {
    $crashDir = Join-Path $ProjectRoot "crashlogs"
    New-Item -ItemType Directory -Force -Path $crashDir | Out-Null
    $stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss_fff"
    $safeContext = ($Context -replace '[^A-Za-z0-9_-]', '_')
    $txtPath = Join-Path $crashDir "$stamp`_$safeContext.txt"
    $mdPath = Join-Path $crashDir "$stamp`_$safeContext.md"
    $message = $ErrorRecord.Exception.Message
    $trace = $ErrorRecord.ScriptStackTrace
    $commandText = if ($ErrorRecord.InvocationInfo) { $ErrorRecord.InvocationInfo.Line } else { "(unknown)" }

    Set-Content -LiteralPath $txtPath -Encoding UTF8 -Value @"
Video2WAV Build Crash/Error Report
==================================

Timestamp: $stamp
Context: $Context
Exception: $($ErrorRecord.Exception.GetType().FullName)
Message: $message

Runtime
-------
Project root: $ProjectRoot
PowerShell: $($PSVersionTable.PSVersion)
Command: $commandText

Script Stack Trace
------------------
$trace

Full Error
----------
$ErrorRecord
"@

    Set-Content -LiteralPath $mdPath -Encoding UTF8 -Value @"
# Video2WAV Build Crash/Error Report

<div style="padding:12px;border-left:6px solid #d64545;background:#2a1111;color:#ffdada;">
<strong>$($ErrorRecord.Exception.GetType().Name)</strong>: $message
</div>

## Summary

| Field | Value |
|---|---|
| Timestamp | ``$stamp`` |
| Context | ``$Context`` |
| Project Root | ``$ProjectRoot`` |
| PowerShell | ``$($PSVersionTable.PSVersion)`` |
| Command | ``$commandText`` |

## Script Stack Trace

````text
$trace
````

## Full Error

````text
$ErrorRecord
````
"@
    Write-Host "Crash report written:" -ForegroundColor Yellow
    Write-Host "  $txtPath"
    Write-Host "  $mdPath"
}

trap {
    Write-CrashReport "build_unhandled_error" $_
    break
}

if (-not (Test-Path $venvPython)) {
    throw "No virtual environment found. Run install_video2wav.bat first."
}

& $venvPython -m pip install --upgrade pyinstaller
& $venvPython -m PyInstaller `
    --clean `
    --onefile `
    --console `
    --name Video2WAV `
    --distpath $ProjectRoot `
    --workpath (Join-Path $ProjectRoot "build") `
    --specpath $ProjectRoot `
    --add-data "src;src" `
    --collect-all yt_dlp `
    --hidden-import yt_dlp `
    --hidden-import requests `
    --hidden-import bs4 `
    --hidden-import tkinter `
    (Join-Path $ProjectRoot "video2wav_launcher.py")

$rootExe = Join-Path $ProjectRoot "Video2WAV.exe"

Write-Host ""
Write-Host "Built executable: $rootExe" -ForegroundColor Green

$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
if ($dotnet) {
    $uninstallProject = Join-Path $ProjectRoot "tools\uninstaller\Video2WAV.Uninstaller.csproj"
    $uninstallPublish = Join-Path $ProjectRoot "build\uninstaller"
    & dotnet publish $uninstallProject -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:EnableCompressionInSingleFile=true -o $uninstallPublish
    $publishedUninstall = Join-Path $uninstallPublish "uninstall.exe"
    if (Test-Path $publishedUninstall) {
        Copy-Item -LiteralPath $publishedUninstall -Destination (Join-Path $ProjectRoot "uninstall.exe") -Force
        Write-Host "Built uninstaller: $(Join-Path $ProjectRoot 'uninstall.exe')" -ForegroundColor Green
    }
} else {
    Write-Host "dotnet was not found; uninstall.exe was not built." -ForegroundColor Yellow
}
