$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Frontend = Join-Path $Root "frontend"

Write-Host "[1/7] Frontend syntax scan"
node scripts/frontend_syntax_check.js

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Host "frontend/node_modules missing; installing dependencies with npm ci"
    Push-Location $Frontend
    try { npm ci --ignore-scripts --no-audit --no-fund } finally { Pop-Location }
}

Write-Host "[2/7] Frontend production build"
Push-Location $Frontend
try {
    $env:CI = "1"
    node .\node_modules\vite\bin\vite.js build
} finally { Pop-Location }


Write-Host "[3/7] Frontend route/static artifact check"
node scripts/frontend_route_check.js

Write-Host "[4/7] Python syntax check"
if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-Path "tests") { py -3 -m compileall -q app tests scripts } else { py -3 -m compileall -q app scripts }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    if (Test-Path "tests") { python -m compileall -q app tests scripts } else { python -m compileall -q app scripts }
} else {
    if (Test-Path "tests") { python3 -m compileall -q app tests scripts } else { python3 -m compileall -q app scripts }
}

Write-Host "[5/7] Environment check"
$env:APP_DATA_DIR = if ($env:APP_DATA_DIR) { $env:APP_DATA_DIR } else { Join-Path $env:TEMP "ops-release-check-data" }
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check_env.ps1

Write-Host "[6/7] Resource check"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/resource_check.ps1

Write-Host "[7/7] DB optimization script smoke"
$env:DATABASE_PATH = Join-Path $env:APP_DATA_DIR "ops.db"
if (Get-Command py -ErrorAction SilentlyContinue) { py -3 scripts/db_optimize.py --check } elseif (Get-Command python -ErrorAction SilentlyContinue) { python scripts/db_optimize.py --check } else { python3 scripts/db_optimize.py --check }
Write-Host "release_check passed"
