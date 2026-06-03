@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%OPS_BASE_URL%"=="" set "OPS_BASE_URL=http://127.0.0.1:8000"
if "%OPS_TOOL_TOKEN%"=="" (
  echo [ERROR] OPS_TOOL_TOKEN is not set
  echo Usage:
  echo   set OPS_BASE_URL=http://127.0.0.1:8000
  echo   set OPS_TOOL_TOKEN=ops_tool_xxx
  echo   scripts\mcp-http-diagnose.bat
  exit /b 1
)

echo [INFO] OPS_BASE_URL=%OPS_BASE_URL%
echo [INFO] OPS_TOOL_TOKEN present: True

echo [MCP HTTP initialize]
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$body = @{ jsonrpc='2.0'; id=1; method='initialize'; params=@{ protocolVersion='2025-06-18'; capabilities=@{}; clientInfo=@{ name='ops-http-diagnose'; version='1.0.0' } } } | ConvertTo-Json -Depth 8; Invoke-RestMethod -Method Post -Uri '%OPS_BASE_URL%/api/v2/mcp' -Headers @{ Authorization='Bearer %OPS_TOOL_TOKEN%'; Accept='application/json, text/event-stream' } -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 20"
if errorlevel 1 exit /b 1

echo [MCP HTTP tools/list]
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$body = @{ jsonrpc='2.0'; id=2; method='tools/list'; params=@{ cursor=$null } } | ConvertTo-Json -Depth 8; Invoke-RestMethod -Method Post -Uri '%OPS_BASE_URL%/api/v2/mcp' -Headers @{ Authorization='Bearer %OPS_TOOL_TOKEN%'; Accept='application/json, text/event-stream' } -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 20"
if errorlevel 1 exit /b 1

echo [OK] Remote HTTP MCP endpoint works
