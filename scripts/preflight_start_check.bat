@echo off
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 scripts\preflight_start_check.py %*
  exit /b %errorlevel%
)
python scripts\preflight_start_check.py %*
exit /b %errorlevel%
