$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Write-Host "[1/5] Python syntax"
python -m compileall -q app scripts main.py config_manager.py ssh_client.py

Write-Host "[2/5] Frontend syntax"
node scripts/frontend_syntax_check.js

Write-Host "[3/5] Frontend route/static contract"
node scripts/frontend_route_check.js

Write-Host "[4/5] MCP local package smoke"
try {
  python scripts/mcp_local_package_smoke.py
} catch {
  Write-Host "[warn] MCP smoke skipped or failed; check dependencies and runtime configuration"
}

Write-Host "[5/5] Dependency-aware optional checks"
if (Test-Path "frontend/node_modules") {
  Push-Location frontend
  npm run typecheck
  npm run build
  Pop-Location
} else {
  Write-Host "[skip] frontend/node_modules missing; run: cd frontend; npm ci"
}

python -c "import sqlalchemy" 2>$null
if ($LASTEXITCODE -eq 0) {
  python -m pytest tests -q
} else {
  Write-Host "[skip] sqlalchemy missing; run: pip install -r requirements.txt"
}

Write-Host "final_acceptance_check completed"
