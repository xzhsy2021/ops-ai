param(
    [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"

$ROOT = Resolve-Path (Join-Path $PSScriptRoot "..")
$DIST = Join-Path $ROOT "dist"
$PKG_NAME = "ops-platform-$Version"
$PKG_DIR = Join-Path $DIST $PKG_NAME

if (Test-Path $PKG_DIR) { Remove-Item $PKG_DIR -Recurse -Force }
New-Item -ItemType Directory -Path $PKG_DIR -Force | Out-Null

$copyItems = @(
    "app",
    "config",
    "docs",
    "tools",
    "scripts",
    "tests",
    "frontend\src",
    "frontend\dist",
    "frontend\index.html",
    "frontend\package.json",
    "frontend\package-lock.json",
    "frontend\tsconfig.json",
    "frontend\tsconfig.node.json",
    "frontend\vite.config.ts",
    "main.py",
    "manage_users.py",
    "ssh_client.py",
    "config_manager.py",
    "requirements.txt",
    "pytest.ini",
    "start.sh",
    "start_prod.sh",
    "start_dev.sh",
    "start_diag.sh",
    "start.bat",
    "start_single_process.bat",
    "start_single_process.ps1",
    ".env.example",
    ".env.local.example",
    ".env.docker.example",
    ".env.windows.example",
    "dev_ops.md",
    "UPGRADE.md"
)

foreach ($item in $copyItems) {
    $src = Join-Path $ROOT $item
    if (Test-Path $src) {
        $dest = Join-Path $PKG_DIR $item
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item -Path $src -Destination $dest -Recurse -Force
    }
}

$excludeDirs = @("__pycache__", ".pytest_cache", ".pytest_tmp", "node_modules", "venv", ".venv", "keys", "archive")
foreach ($dirName in $excludeDirs) {
    Get-ChildItem -Path $PKG_DIR -Directory -Recurse -Filter $dirName | Remove-Item -Recurse -Force
}

$excludeFiles = @("*.pyc", "*.log", "ops.db", "ops.db-shm", "ops.db-wal", ".env", "*.pem", "*.key")
foreach ($pattern in $excludeFiles) {
    Get-ChildItem -Path $PKG_DIR -File -Recurse -Filter $pattern | Remove-Item -Force
}

$zipPath = Join-Path $DIST "$PKG_NAME.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$venvPython = Join-Path $ROOT "venv\Scripts\python.exe"
$zipHelper = Join-Path $ROOT "scripts\create_package_zip.py"
if (Test-Path $venvPython) {
    & $venvPython $zipHelper --root $ROOT --package-dir $PKG_DIR --output $zipPath
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $zipHelper --root $ROOT --package-dir $PKG_DIR --output $zipPath
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $zipHelper --root $ROOT --package-dir $PKG_DIR --output $zipPath
} else {
    throw "Python not found"
}
if ($LASTEXITCODE -ne 0) { throw "Failed to create package ZIP" }

Write-Host "Package created: $zipPath"
