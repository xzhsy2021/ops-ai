$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$AppData = if ($env:APP_DATA_DIR) { $env:APP_DATA_DIR } else { Join-Path $Root "data" }
$BackupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $AppData "backups" }
$Ts = Get-Date -Format "yyyyMMdd_HHmmss"
$Out = Join-Path $BackupDir "pre_upgrade_$Ts.zip"

if (-not (Test-Path $AppData)) { New-Item -ItemType Directory -Path $AppData -Force | Out-Null }
if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null }

Write-Host "[backup] APP_DATA_DIR=$AppData"
Write-Host "[backup] output=$Out"
Compress-Archive -Path (Join-Path $AppData "*") -DestinationPath $Out -Force
Write-Host "[OK] pre-upgrade backup created: $Out"
