@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
set SCRIPT_DIR=%~dp0
set ROOT=%SCRIPT_DIR%..
for %%I in ("%ROOT%") do set ROOT=%%~fI
cd /d "%ROOT%"
if not defined OPS_BASE_URL set OPS_BASE_URL=http://127.0.0.1:8000
if not defined OPS_MCP_HTTP_TIMEOUT set OPS_MCP_HTTP_TIMEOUT=6
if not defined OPS_MCP_ASCII_DESCRIPTIONS set OPS_MCP_ASCII_DESCRIPTIONS=1
set PYTHONIOENCODING=utf-8
set PYEXE=
if exist "%ROOT%\venv\Scripts\python.exe" set PYEXE=%ROOT%\venv\Scripts\python.exe
if not defined PYEXE (
  where py >nul 2>&1
  if not errorlevel 1 set PYEXE=py -3
)
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%P | findstr /I "\\WindowsApps\\" >nul 2>&1
    if errorlevel 1 if not defined PYEXE set PYEXE=%%P
  )
)
if not defined PYEXE (
  echo [FAIL] Python 3.10+ not found.
  exit /b 1
)
echo [OK] Python: %PYEXE%
echo [INFO] OPS_BASE_URL=%OPS_BASE_URL%
%PYEXE% scripts\mcp_diagnose.py
