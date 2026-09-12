$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$status = 0
function Ok($msg) { Write-Host "[OK] $msg" }
function Warn($msg) { Write-Host "[WARN] $msg" }
function Fail($msg) { Write-Host "[FAIL] $msg"; $script:status = 1 }

$PythonExe = $null
$PythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
    $PythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $PythonExe = "python3"
}

if ($PythonExe) {
    Ok "Python available"
    & $PythonExe @PythonArgs -c "import sys; assert sys.version_info >= (3, 9), 'Python 3.9+ required'; print('[OK] Python ' + sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) { Fail "Python 3.9+ required" }
} else {
    Fail "Python not found"
}

$AppData = if ($env:APP_DATA_DIR) { $env:APP_DATA_DIR } else { Join-Path $Root "data" }
$Upload = if ($env:UPLOAD_DIR) { $env:UPLOAD_DIR } else { Join-Path $AppData "uploads" }
$Keys = if ($env:KEYS_DIR) { $env:KEYS_DIR } else { Join-Path $AppData "keys" }
$Backup = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $AppData "backups" }
$Log = if ($env:LOG_DIR) { $env:LOG_DIR } else { Join-Path $AppData "logs" }
$Runtime = if ($env:RUNTIME_DIR) { $env:RUNTIME_DIR } else { Join-Path $AppData "runtime" }
$Db = if ($env:OPS_DB_PATH) { $env:OPS_DB_PATH } else { Join-Path $AppData "ops.db" }

foreach ($dir in @($AppData, $Upload, $Keys, $Backup, $Log, $Runtime)) {
    try {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $testFile = Join-Path $dir ".write-test"
        Set-Content -Path $testFile -Value "ok" -Encoding UTF8
        Remove-Item $testFile -Force
        Ok "writable directory: $dir"
    } catch {
        Fail "directory is not writable: $dir"
    }
}

# 占位/示例值检测：与 app/core/secrets_policy.py 的 INSECURE_MARKERS 保持一致。
# 历史缺陷：这里只判断"是否设置 + 长度"，于是与仓库 .env.example 逐字节相同的占位
# 密钥（可伪造会话令牌 / 可解密库内凭据）会被报成 OK。
$InsecureMarkers = @('change-me', 'change_me', 'changeme', 'please-change', 'please-generate',
    'replace-me', 'replace_me', 'your-secret', 'your_secret', 'yoursecret', 'your-key',
    'your_key', 'placeholder', 'do-not-use-in-production', 'dev-fallback')

function Test-InsecureSecret([string]$Value) {
    if (-not $Value) { return $false }
    $lower = $Value.ToLower()
    foreach ($marker in $InsecureMarkers) { if ($lower.Contains($marker)) { return $true } }
    return $false
}

function Test-SecretKey([string]$Name, [string]$Value, [bool]$Required) {
    if (-not $Value) {
        if ($Required) { Fail "$Name is not set; it is required (production)"; return }
        Warn "$Name not set; local dev works, but stored credentials may use compatibility mode"
        return
    }
    if (Test-InsecureSecret $Value) {
        Fail "$Name looks like a public example/placeholder value; rotate it (python scripts/rotate_secrets.py --rotate-$($Name.ToLower()) --apply)"
        return
    }
    if ($Value.Length -lt 32) { Warn "$Name is set but short ($($Value.Length)); use at least 32 random chars"; return }
    Ok "$Name configured"
}

Test-SecretKey "SESSION_SECRET" $env:SESSION_SECRET $false
Test-SecretKey "OPS_SECRET_KEY" $env:OPS_SECRET_KEY $false
Ok "密钥体检（权威判定）：python scripts/rotate_secrets.py --check"

if (Test-Path (Join-Path $Root "requirements.txt")) { Ok "requirements.txt found" } else { Fail "requirements.txt missing" }
Write-Host "APP_DATA_DIR=$AppData"
Write-Host "UPLOAD_DIR=$Upload"
Write-Host "KEYS_DIR=$Keys"
Write-Host "BACKUP_DIR=$Backup"
Write-Host "LOG_DIR=$Log"
Write-Host "RUNTIME_DIR=$Runtime"
Write-Host "OPS_DB_PATH=$Db"
exit $status
