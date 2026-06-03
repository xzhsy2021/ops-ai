param(
    [string]$HostName = $(if ($env:HOST) { $env:HOST } else { "0.0.0.0" }),
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 }),
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            & py -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0) { return "py -3" }
        } catch {}
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return "python" }
    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) { return "python3" }
    throw "Python 3.10+ not found. Install Python and disable Microsoft Store app execution aliases if needed."
}

$PythonCmd = Find-Python
$Venv = Join-Path $Root "venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPip = Join-Path $Venv "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[single] creating virtual environment"
    Invoke-Expression "$PythonCmd -m venv `"$Venv`""
}

Write-Host "[single] checking backend dependencies"
& $VenvPython -c "import fastapi,uvicorn,paramiko,pydantic,sqlalchemy,yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $VenvPip install -r (Join-Path $Root "requirements.txt") --no-warn-script-location
}

$env:APP_DATA_DIR = if ($env:APP_DATA_DIR) { $env:APP_DATA_DIR } else { Join-Path $Root "data" }
$env:UPLOAD_DIR = if ($env:UPLOAD_DIR) { $env:UPLOAD_DIR } else { Join-Path $env:APP_DATA_DIR "uploads" }
$env:KEYS_DIR = if ($env:KEYS_DIR) { $env:KEYS_DIR } else { Join-Path $env:APP_DATA_DIR "keys" }
$env:BACKUP_DIR = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $env:APP_DATA_DIR "backups" }
$env:LOG_DIR = if ($env:LOG_DIR) { $env:LOG_DIR } else { Join-Path $env:APP_DATA_DIR "logs" }
$env:RUNTIME_DIR = if ($env:RUNTIME_DIR) { $env:RUNTIME_DIR } else { Join-Path $env:APP_DATA_DIR "runtime" }
$env:REPORT_DIR = if ($env:REPORT_DIR) { $env:REPORT_DIR } else { Join-Path $env:APP_DATA_DIR "reports" }
$env:SERVE_FRONTEND = if ($env:SERVE_FRONTEND) { $env:SERVE_FRONTEND } else { "true" }
$env:ENV = if ($env:ENV) { $env:ENV } else { "local" }
$env:HOST = $HostName
$env:PORT = [string]$Port
$env:MAX_TERMINAL_SESSIONS = if ($env:MAX_TERMINAL_SESSIONS) { $env:MAX_TERMINAL_SESSIONS } else { "3" }
$env:MAX_LOG_TAIL_LINES = if ($env:MAX_LOG_TAIL_LINES) { $env:MAX_LOG_TAIL_LINES } else { "500" }
$env:TASK_IDLE_POLL_SECONDS = if ($env:TASK_IDLE_POLL_SECONDS) { $env:TASK_IDLE_POLL_SECONDS } else { "15" }

foreach ($dir in @($env:APP_DATA_DIR, $env:UPLOAD_DIR, $env:KEYS_DIR, $env:BACKUP_DIR, $env:LOG_DIR, $env:RUNTIME_DIR, $env:REPORT_DIR)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

$FrontendDist = Join-Path $Root "frontend\dist"
if (-not $SkipFrontendBuild -and -not (Test-Path $FrontendDist)) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw "frontend/dist not found and npm is unavailable. Install Node.js 18+ or build frontend on another machine." }
    Write-Host "[single] building frontend"
    Push-Location (Join-Path $Root "frontend")
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        $env:CI = "1"
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally {
        Pop-Location
    }
}

Write-Host "[single] running startup preflight"
& $VenvPython scripts/preflight_start_check.py --host $HostName --port $Port --require-dist
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[single] running local upgrade/data check"
& $VenvPython scripts/migrate_check.py --app-data-dir $env:APP_DATA_DIR
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[single] starting OPS on http://$HostName`:$Port"
Write-Host "[single] data: $env:APP_DATA_DIR"
& $VenvPython -m uvicorn main:app --host $HostName --port $Port
