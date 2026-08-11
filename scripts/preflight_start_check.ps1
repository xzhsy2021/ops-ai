param(
    [string]$HostName = "",
    [Nullable[int]]$Port = $null,
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
if ($env:OPS_DOTENV_LOADED -ne "1") {
    $DotEnvPath = Join-Path $Root ".env"
    $LoaderArgs = @("scripts/load_dotenv.py", "--format", "json", $DotEnvPath)
    if ($python.Name -eq "py.exe" -or $python.Name -eq "py") {
        $DotEnvJson = & py -3 @LoaderArgs
    } else {
        $DotEnvJson = & $python.Source @LoaderArgs
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to load $DotEnvPath" }
    $DotEnvValues = $DotEnvJson | ConvertFrom-Json
    foreach ($property in $DotEnvValues.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, "Process")
    }
    $env:OPS_DOTENV_LOADED = "1"
}
if (-not $HostName) {
    $HostName = if ($env:HOST) { $env:HOST } elseif ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "0.0.0.0" }
}
if ($null -eq $Port) {
    $Port = if ($env:PORT) { [int]$env:PORT } elseif ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }
}
$argsList = @("scripts/preflight_start_check.py", "--host", $HostName, "--port", [string]$Port)
if ($RequireDist) { $argsList += "--require-dist" }
if ($Json) { $argsList += "--json" }
if ($python.Name -eq "py.exe" -or $python.Name -eq "py") {
    & py -3 @argsList
} else {
    & $python.Source @argsList
}
exit $LASTEXITCODE
