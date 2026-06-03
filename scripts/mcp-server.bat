@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

set SCRIPT_DIR=%~dp0
set ROOT=%SCRIPT_DIR%..
for %%I in ("%ROOT%") do set ROOT=%%~fI
set VENV_PY=%ROOT%\venv\Scripts\python.exe

cd /d "%ROOT%"

if not defined OPS_BASE_URL set OPS_BASE_URL=http://127.0.0.1:8000
if not defined OPS_MCP_HTTP_TIMEOUT set OPS_MCP_HTTP_TIMEOUT=6
if not defined OPS_MCP_ASCII_DESCRIPTIONS set OPS_MCP_ASCII_DESCRIPTIONS=1
set PYTHONIOENCODING=utf-8

if exist "%VENV_PY%" (
    "%VENV_PY%" -m app.mcp.server
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        py -3 -m app.mcp.server
        exit /b %ERRORLEVEL%
    )
)

rem Avoid the Microsoft Store python.exe execution alias under WindowsApps.
for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%P | findstr /I "\\WindowsApps\\" >nul 2>&1
    if errorlevel 1 (
        "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            "%%P" -m app.mcp.server
            exit /b %ERRORLEVEL%
        )
    )
)

echo [ERROR] Python 3.10+ was not found. 1>&2
echo Start OPS once with start.bat to create venv, or install Python from https://www.python.org/downloads/. 1>&2
echo If Windows opens Microsoft Store, disable App execution aliases for python.exe/python3.exe. 1>&2
exit /b 1
