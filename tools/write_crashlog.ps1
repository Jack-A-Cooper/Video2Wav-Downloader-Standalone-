param(
    [Parameter(Mandatory = $true)]
    [string]$Context,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Details = ""
)

# Lightweight crashlog writer used by batch launchers before Python is available.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$CrashDir = Join-Path $ProjectRoot "crashlogs"
New-Item -ItemType Directory -Force -Path $CrashDir | Out-Null

$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss_fff"
$SafeContext = ($Context -replace '[^A-Za-z0-9_-]', '_')
$TxtPath = Join-Path $CrashDir "$Stamp`_$SafeContext.txt"
$MdPath = Join-Path $CrashDir "$Stamp`_$SafeContext.md"

Set-Content -LiteralPath $TxtPath -Encoding UTF8 -Value @"
Video2WAV Launcher Crash/Error Report
=====================================

Timestamp: $Stamp
Context: $Context
Message: $Message

Runtime
-------
Project root: $ProjectRoot
PowerShell: $($PSVersionTable.PSVersion)
Working directory: $(Get-Location)

Details
-------
$Details
"@

Set-Content -LiteralPath $MdPath -Encoding UTF8 -Value @"
# Video2WAV Launcher Crash/Error Report

<div style="padding:12px;border-left:6px solid #d64545;background:#2a1111;color:#ffdada;">
<strong>$Context</strong>: $Message
</div>

## Summary

| Field | Value |
|---|---|
| Timestamp | ``$Stamp`` |
| Context | ``$Context`` |
| Project Root | ``$ProjectRoot`` |
| Working Directory | ``$(Get-Location)`` |
| PowerShell | ``$($PSVersionTable.PSVersion)`` |

## Details

````text
$Details
````
"@

Write-Host "Crash report written:"
Write-Host "  $TxtPath"
Write-Host "  $MdPath"
