param(
    [string]$HostName = $(if ($env:HOST) { $env:HOST } elseif ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "0.0.0.0" }),
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } elseif ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }),
    [switch]$RequireDist,
    [switch]$Json
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found" }
$argsList = @("scripts/preflight_start_check.py", "--host", $HostName, "--port", [string]$Port)
if ($RequireDist) { $argsList += "--require-dist" }
if ($Json) { $argsList += "--json" }
if ($python.Name -eq "py.exe" -or $python.Name -eq "py") {
    & py -3 @argsList
} else {
    & $python.Source @argsList
}
exit $LASTEXITCODE
