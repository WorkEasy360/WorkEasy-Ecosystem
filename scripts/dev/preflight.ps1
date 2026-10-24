<#
.SYNOPSIS
  Checks what the local Workeasy360 backend needs before it starts: the Docker
  engine, the PostgreSQL and PgBouncer containers, the database ports, and a real
  Django database connection.

.DESCRIPTION
  Read-only by default. With -Fix it does the one safe repair that is usually
  needed after a Docker restart: `docker compose -p workeasy360 start`, which
  starts the EXISTING Workeasy360 containers (they have no restart policy). It
  never recreates, removes or resets a container, volume or database, never
  restarts Docker Desktop (that would also stop other projects' containers), and
  never prints a password.

  Exit code 0 when everything is ready, 1 otherwise.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\preflight.ps1
  powershell -ExecutionPolicy Bypass -File scripts\dev\preflight.ps1 -Fix
#>
param([switch]$Fix)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Backend = Join-Path $Root "backend"
$Containers = @("workeasy360-postgres-1", "workeasy360-pgbouncer-1")
$script:Failed = $false

function Report([string]$Name, [bool]$Ok, [string]$Detail, [string]$Hint = "") {
    $mark = if ($Ok) { "OK  " } else { "FAIL" }
    Write-Host ("[{0}] {1,-24} {2}" -f $mark, $Name, $Detail)
    if (-not $Ok) {
        $script:Failed = $true
        if ($Hint) { Write-Host ("       -> {0}" -f $Hint) }
    }
}

# A command that can hang (a stuck engine) runs in a job with a deadline.
function Invoke-WithTimeout([scriptblock]$Block, [int]$Seconds) {
    $job = Start-Job -ScriptBlock $Block
    if (Wait-Job $job -Timeout $Seconds) {
        $out = Receive-Job $job 2>&1 | Out-String
        Remove-Job $job -Force
        return @{ TimedOut = $false; Output = $out.Trim() }
    }
    Stop-Job $job; Remove-Job $job -Force
    return @{ TimedOut = $true; Output = "" }
}

# The ports the backend uses, read from .env (names only; secrets are not read out).
function Get-EnvValue([string]$Key, [string]$Default) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern "^\s*$Key\s*=" | Select-Object -First 1
        if ($line) { return ($line.Line -split "=", 2)[1].Trim().Trim('"') }
    }
    return $Default
}
$PoolPort = [int](Get-EnvValue "W360_DB_PORT" "6442")
$DirectPort = [int](Get-EnvValue "W360_DB_DIRECT_PORT" "5442")

Write-Host "Workeasy360 backend preflight"

# 1. Docker engine ------------------------------------------------------------------
$engine = Invoke-WithTimeout { docker info --format "{{.ServerVersion}}" } 20
$engineOk = (-not $engine.TimedOut) -and ($engine.Output -match "^\d+\.\d+")
if ($engine.TimedOut) {
    Report "Docker engine" $false "no answer in 20 s" "Docker Desktop is hung: tray icon > Restart (this also restarts other projects' containers)."
} elseif (-not $engineOk) {
    $first = ($engine.Output -split "`n")[0]
    if ($first.Length -gt 110) { $first = $first.Substring(0, 110) + "..." }
    Report "Docker engine" $false $first "Start or restart Docker Desktop (tray icon > Restart), then run this again."
} else {
    Report "Docker engine" $true ("running, version " + $engine.Output)
}

# 2. Containers ----------------------------------------------------------------------
if ($engineOk) {
    $states = @{}
    foreach ($name in $Containers) {
        $s = (docker inspect -f "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}" $name 2>$null)
        $states[$name] = if ($s) { $s } else { "missing|" }
    }
    $stopped = @($Containers | Where-Object { -not $states[$_].StartsWith("running") -and -not $states[$_].StartsWith("missing") })
    if ($stopped.Count -gt 0 -and $Fix) {
        Write-Host "       -> starting the existing Workeasy360 containers (docker compose -p workeasy360 start)"
        Push-Location $Root
        try { docker compose -p workeasy360 start | Out-Null } finally { Pop-Location }
        for ($i = 0; $i -lt 30; $i++) {
            $health = docker inspect -f "{{.State.Health.Status}}" "workeasy360-postgres-1" 2>$null
            if ($health -eq "healthy") { break }
            Start-Sleep -Seconds 2
        }
        foreach ($name in $Containers) {
            $states[$name] = (docker inspect -f "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}" $name 2>$null)
        }
    }
    foreach ($name in $Containers) {
        $parts = $states[$name].Split("|")
        $ok = ($parts[0] -eq "running") -and ($parts[1] -eq "" -or $parts[1] -eq "healthy")
        $detail = if ($parts[1]) { "$($parts[0]) ($($parts[1]))" } else { $parts[0] }
        $hint = if ($parts[0] -eq "missing") { "Container not found: create it with 'docker compose -p workeasy360 up -d' (no volume is touched)." } else { "Run this script with -Fix, or: docker compose -p workeasy360 start" }
        Report $name $ok $detail $hint
    }
}

# 3. Ports ---------------------------------------------------------------------------
foreach ($p in @(@{ Name = "PgBouncer port"; Port = $PoolPort }, @{ Name = "PostgreSQL port"; Port = $DirectPort })) {
    $t = Test-NetConnection -ComputerName 127.0.0.1 -Port $p.Port -WarningAction SilentlyContinue
    Report $p.Name $t.TcpTestSucceeded ("127.0.0.1:" + $p.Port) "Nothing listens there: the containers are not running."
}

# 4. A real Django database connection (TCP alone is not enough: a hung engine
#    still accepts connections on its forwarded ports and then times out).
$probe = "from django.db import connection; connection.ensure_connection(); c = connection.cursor(); c.execute('select current_database()'); print('connected to', c.fetchone()[0], 'via', connection.settings_dict['HOST'] + ':' + str(connection.settings_dict['PORT']))"
$django = Invoke-WithTimeout ([scriptblock]::Create("Set-Location '$Backend'; & '$Python' manage.py shell -c `"$probe`" 2>&1")) 60
$line = ($django.Output -split "`n" | Where-Object { $_ -match "connected to|Error|timeout" } | Select-Object -Last 1)
$djangoOk = (-not $django.TimedOut) -and ($django.Output -match "connected to")
if ($django.TimedOut) { $line = "no answer in 60 s" }
if ($line -and $line.Length -gt 110) { $line = $line.Substring(0, 110) + "..." }
Report "Django database" $djangoOk ([string]$line) "See the checks above; a connection timeout with open ports usually means a hung Docker engine."

Write-Host ""
if ($script:Failed) { Write-Host "Not ready."; exit 1 }
Write-Host "Ready: start the backend with  python manage.py runserver 127.0.0.1:8010"
exit 0
