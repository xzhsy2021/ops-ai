param(
    [string]$HostName = $(if ($env:HOST) { $env:HOST } else { "0.0.0.0" }),
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 }),
    [switch]$SkipFrontendBuild
)
& (Join-Path $PSScriptRoot "scripts\start_single_process.ps1") -HostName $HostName -Port $Port -SkipFrontendBuild:$SkipFrontendBuild
