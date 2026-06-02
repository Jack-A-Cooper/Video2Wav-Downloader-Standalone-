param(
    [switch]$SkipExeBuild
)

# Windows installer/bootstrapper for Video2WAV.
#
# Responsibilities:
# - locate a supported Python runtime or direct the user to install one
# - create or repair the local virtual environment
# - install/upgrade Python dependencies
# - verify FFmpeg/FFprobe availability
# - write convenience launchers
# - build Video2WAV.exe and uninstall.exe unless explicitly skipped

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot
$RequiredPython = [version]"3.9.0"
$PythonDownloadUrl = "https://www.python.org/downloads/windows/"
$FfmpegDownloadUrl = "https://www.gyan.dev/ffmpeg/builds/"

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

    $txt = @"
Video2WAV Installer Crash/Error Report
======================================

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
    Set-Content -LiteralPath $txtPath -Encoding UTF8 -Value $txt

    $md = @"
# Video2WAV Installer Crash/Error Report

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
    Set-Content -LiteralPath $mdPath -Encoding UTF8 -Value $md
    Write-Host "Crash report written:" -ForegroundColor Yellow
    Write-Host "  $txtPath"
    Write-Host "  $mdPath"
}

trap {
    Write-CrashReport "installer_unhandled_error" $_
    break
}

function Write-Step($Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Test-PythonCandidate($Command, $Args) {
    # Probe a possible Python command without assuming that py.exe or python.exe
    # are both available on every Windows installation.
    try {
        $versionText = & $Command @Args -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if (-not $versionText) { return $null }
        $version = [version]$versionText.Trim()
        if ($version -ge $RequiredPython) {
            $exe = & $Command @Args -c "import sys; print(sys.executable)" 2>$null
            return [pscustomobject]@{ Command = $Command; Args = $Args; Version = $version; Exe = $exe.Trim() }
        }
    } catch {
        return $null
    }
    return $null
}

function Find-Python {
    # Prefer explicit py launcher versions so an older default python.exe does
    # not hide a newer installed interpreter.
    $candidates = @(
        [pscustomobject]@{ Command = "py"; Args = @("-3.12") },
        [pscustomobject]@{ Command = "py"; Args = @("-3.11") },
        [pscustomobject]@{ Command = "py"; Args = @("-3.10") },
        [pscustomobject]@{ Command = "py"; Args = @("-3.9") },
        [pscustomobject]@{ Command = "py"; Args = @() },
        [pscustomobject]@{ Command = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        $found = Test-PythonCandidate $candidate.Command $candidate.Args
        if ($found) { return $found }
    }
    return $null
}

Write-Step "Checking Python"
$python = Find-Python
if (-not $python) {
    Write-Host "Python $RequiredPython or newer was not found." -ForegroundColor Yellow
    Write-Host "Install the latest stable Python for Windows, then rerun this installer:"
    Write-Host $PythonDownloadUrl -ForegroundColor Green
    Start-Process $PythonDownloadUrl
    exit 1
}
Write-Host "Using Python $($python.Version): $($python.Exe)"

Write-Step "Creating virtual environment"
$venvDir = Join-Path $ProjectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvHealthy = $false
if (Test-Path $venvPython) {
    try {
        & $venvPython -c "import sys; print(sys.version)" | Out-Null
        if ($LASTEXITCODE -eq 0) { $venvHealthy = $true }
    } catch {
        $venvHealthy = $false
    }
}
if ((Test-Path $venvDir) -and -not $venvHealthy) {
    # A moved/uninstalled base Python can leave venv launchers pointing at a
    # dead interpreter. Recreating the venv is safer than trying to repair it.
    Write-Host "Existing virtual environment is broken or stale. Recreating it..." -ForegroundColor Yellow
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}
if (-not (Test-Path $venvDir)) {
    & $python.Command @($python.Args + @("-m", "venv", $venvDir))
}
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment Python was not created at $venvPython"
}

Write-Step "Installing Python packages"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --upgrade -r (Join-Path $ProjectRoot "requirements.txt")
& $venvPython -m pip install --upgrade pyinstaller

Write-Step "Checking FFmpeg"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
    # FFmpeg is an external binary dependency. The installer offers winget when
    # available, but keeps a manual download path for machines without winget.
    Write-Host "FFmpeg and/or FFprobe were not found on PATH." -ForegroundColor Yellow
    Write-Host "Video2WAV needs both for WAV conversion and media inspection."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-Host "Install FFmpeg with winget now? (Y/N)"
        if ($answer -match "^[Yy]") {
            winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
            $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $env:Path = "$machinePath;$userPath"
            Write-Host "Restart your terminal after winget finishes so PATH updates are visible."
        } else {
            Write-Host "Manual FFmpeg download: $FfmpegDownloadUrl" -ForegroundColor Green
            Start-Process $FfmpegDownloadUrl
        }
    } else {
        Write-Host "Manual FFmpeg download: $FfmpegDownloadUrl" -ForegroundColor Green
        Start-Process $FfmpegDownloadUrl
    }
} else {
    Write-Host "FFmpeg found: $($ffmpeg.Source)"
    Write-Host "FFprobe found: $($ffprobe.Source)"
}

Write-Step "Writing local launchers"
# These launchers intentionally check venv health before running the Python app
# so users get a clear reinstall message instead of a low-level process error.
$cmdLauncher = Join-Path $ProjectRoot "Video2WAV_CMD.bat"
$guiLauncher = Join-Path $ProjectRoot "Video2WAV_GUI.bat"
Set-Content -LiteralPath $cmdLauncher -Encoding ASCII -Value "@echo off`r`ncd /d `"%~dp0`"`r`nif not exist `"%~dp0.venv\Scripts\python.exe`" goto missing_venv`r`n`"%~dp0.venv\Scripts\python.exe`" -c `"import sys`" >nul 2>nul`r`nif errorlevel 1 goto missing_venv`r`n`"%~dp0.venv\Scripts\python.exe`" video2wav.py`r`npause`r`nexit /b`r`n:missing_venv`r`necho Video2WAV virtual environment is missing or broken.`r`necho Run install_video2wav.bat, then try again.`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0tools\write_crashlog.ps1`" -Context `"cmd_launcher_missing_or_broken_venv`" -Message `"Video2WAV virtual environment is missing or broken.`" -Details `"Launcher: Video2WAV_CMD.bat`"`r`npause`r`n"
Set-Content -LiteralPath $guiLauncher -Encoding ASCII -Value "@echo off`r`ncd /d `"%~dp0`"`r`nif not exist `"%~dp0.venv\Scripts\python.exe`" goto missing_venv`r`n`"%~dp0.venv\Scripts\python.exe`" -c `"import sys`" >nul 2>nul`r`nif errorlevel 1 goto missing_venv`r`n`"%~dp0.venv\Scripts\python.exe`" video2wav.py --gui`r`nexit /b`r`n:missing_venv`r`necho Video2WAV virtual environment is missing or broken.`r`necho Run install_video2wav.bat, then try again.`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0tools\write_crashlog.ps1`" -Context `"gui_launcher_missing_or_broken_venv`" -Message `"Video2WAV virtual environment is missing or broken.`" -Details `"Launcher: Video2WAV_GUI.bat`"`r`npause`r`n"

if (-not $SkipExeBuild) {
    Write-Step "Building Video2WAV.exe"
    & (Join-Path $ProjectRoot "tools\build_video2wav_exe.ps1")
}

Write-Step "Install complete"
Write-Host "Project: $ProjectRoot"
Write-Host "CMD launcher: $cmdLauncher"
Write-Host "GUI launcher: $guiLauncher"
Write-Host "Executable, if build succeeded: $(Join-Path $ProjectRoot 'Video2WAV.exe')"
Write-Host "Uninstaller, if build succeeded: $(Join-Path $ProjectRoot 'uninstall.exe')"
