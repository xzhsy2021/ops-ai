$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$AppData = if ($env:APP_DATA_DIR) { $env:APP_DATA_DIR } else { Join-Path $Root "data" }
$MaxLogTailLines = if ($env:MAX_LOG_TAIL_LINES) { $env:MAX_LOG_TAIL_LINES } else { "500" }
$MaxTerminalSessions = if ($env:MAX_TERMINAL_SESSIONS) { $env:MAX_TERMINAL_SESSIONS } else { "3" }
$TaskIdlePollSeconds = if ($env:TASK_IDLE_POLL_SECONDS) { $env:TASK_IDLE_POLL_SECONDS } else { "15" }

Write-Host "[resource] root=$Root"
Write-Host "[resource] APP_DATA_DIR=$AppData"
Write-Host "[resource] MAX_LOG_TAIL_LINES=$MaxLogTailLines"
Write-Host "[resource] MAX_TERMINAL_SESSIONS=$MaxTerminalSessions"
Write-Host "[resource] TASK_IDLE_POLL_SECONDS=$TaskIdlePollSeconds"

function Show-DirUsage($path) {
    if (-not (Test-Path $path)) { return }
    $sum = 0
    $count = 0
    Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $sum += $_.Length
        $count += 1
    }
    $mb = [Math]::Round($sum / 1MB, 2)
    Write-Host "[resource] $path : $mb MB, files=$count"
}

if (Test-Path $AppData) {
    Show-DirUsage $AppData
    foreach ($name in @("uploads", "logs", "backups", "runtime", "keys")) { Show-DirUsage (Join-Path $AppData $name) }
} else {
    Write-Host "[resource] data directory does not exist yet"
}

$PythonExe = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
if ($PythonExe -eq "py") {
    & py -3 -m py_compile app/services/runtime_resources.py app/services/tool_adapters/runtime_tools.py app/api/system.py app/core/platform.py
} else {
    & $PythonExe -m py_compile app/services/runtime_resources.py app/services/tool_adapters/runtime_tools.py app/api/system.py app/core/platform.py
}
node scripts/frontend_syntax_check.js
Write-Host "[resource] lightweight resource check passed"
