<#
.SYNOPSIS
  Runs the preflight (starting the existing Workeasy360 containers if they are
  stopped) and, when everything is ready, starts the Django dev server on
  127.0.0.1:8010 - the address `next dev` proxies to.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\start-backend.ps1
#>
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
& (Join-Path $PSScriptRoot "preflight.ps1") -Fix
if ($LASTEXITCODE -ne 0) {
    Write-Host "The backend was not started: fix what the preflight reported, then run this again."
    exit 1
}
Set-Location (Join-Path $Root "backend")
& (Join-Path $Root ".venv\Scripts\python.exe") manage.py runserver 127.0.0.1:8010
