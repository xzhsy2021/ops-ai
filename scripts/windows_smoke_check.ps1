param(
    [string]$BaseUrl = $(if ($env:OPS_BASE_URL) { $env:OPS_BASE_URL } else { "http://localhost:8000" })
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Find-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return @("python3") }
    throw "Python not found"
}

$cmd = Find-PythonCommand
Write-Host "[smoke] Checking $BaseUrl"
if ($cmd.Length -gt 1) {
    & $cmd[0] $cmd[1] scripts/smoke_check.py --base-url $BaseUrl
} else {
    & $cmd[0] scripts/smoke_check.py --base-url $BaseUrl
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "windows_smoke_check passed"
