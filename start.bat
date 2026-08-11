@echo off
setlocal
if not "%OPS_DOTENV_LOADED%"=="1" (
    set "OPS_DOTENV_LOADED=1"
    py -3 "%~dp0scripts\load_dotenv.py" "%~dp0.env" --run "%ComSpec%" /d /c call "%~f0" %*
    if errorlevel 1 (
        endlocal
        exit /b 1
    )
    endlocal
    exit /b 0
)
endlocal
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Ops Platform v2.1.6

set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
set VENV=%ROOT%\venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe
set FRONTEND_DIR=%ROOT%\frontend

if not defined BACKEND_PORT set BACKEND_PORT=8000
if not defined BACKEND_HOST set BACKEND_HOST=0.0.0.0
if not defined FRONTEND_PORT set FRONTEND_PORT=3000
if not defined APP_DATA_DIR set APP_DATA_DIR=%ROOT%\data
if not defined UPLOAD_DIR set UPLOAD_DIR=%APP_DATA_DIR%\uploads
if not defined KEYS_DIR set KEYS_DIR=%APP_DATA_DIR%\keys
if not defined BACKUP_DIR set BACKUP_DIR=%APP_DATA_DIR%\backups
if not defined LOG_DIR set LOG_DIR=%APP_DATA_DIR%\logs
if not defined REPORT_DIR set REPORT_DIR=%APP_DATA_DIR%\reports

echo.
echo   ========================================
echo     Ops Platform v2.1.6
echo     DevOps
echo   ========================================
echo.

echo [1/7] Checking Python...
where py >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] py launcher not found
    echo   Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('py -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do set PY_VER=%%v
echo   Python %PY_VER% found

echo [2/7] Checking virtual environment...
if not exist "%PYTHON%" (
    echo   Creating virtual environment...
    py -m venv "%VENV%"
    if errorlevel 1 (
        echo   [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo   Virtual environment created
) else (
    echo   Virtual environment OK
)

echo [3/7] Checking backend dependencies...
"%PYTHON%" -c "import fastapi,uvicorn,paramiko,pydantic,sqlalchemy,yaml" >nul 2>&1
if errorlevel 1 (
    echo   Installing backend dependencies...
    "%PIP%" install -r "%ROOT%\requirements.txt" -q
    if errorlevel 1 (
        echo   [ERROR] Failed to install backend dependencies
        pause
        exit /b 1
    )
    echo   Backend dependencies installed
) else (
    echo   Backend dependencies OK
)

echo [4/7] Checking frontend dependencies...
where node >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Node.js not found
    echo   Install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] npm not found
    echo   Install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)
if not exist "%FRONTEND_DIR%\package.json" (
    echo   [ERROR] frontend/package.json not found
    pause
    exit /b 1
)
cd /d "%FRONTEND_DIR%"
set NEED_FRONTEND_INSTALL=0
if not exist "%FRONTEND_DIR%\node_modules" (
    echo   node_modules not found
    set NEED_FRONTEND_INSTALL=1
)
findstr /i /c:"lucide-react" "%FRONTEND_DIR%\package.json" >nul 2>&1
if not errorlevel 1 (
    if not exist "%FRONTEND_DIR%\node_modules\lucide-react\package.json" (
        echo   Missing declared npm package: lucide-react
        set NEED_FRONTEND_INSTALL=1
    )
)
findstr /i /c:"framer-motion" "%FRONTEND_DIR%\package.json" >nul 2>&1
if not errorlevel 1 (
    if not exist "%FRONTEND_DIR%\node_modules\framer-motion\package.json" (
        echo   Missing declared npm package: framer-motion
        set NEED_FRONTEND_INSTALL=1
    )
)
if "%NEED_FRONTEND_INSTALL%"=="1" (
    echo   Installing frontend dependencies from package.json...
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo   [ERROR] Failed to install frontend dependencies
        pause
        exit /b 1
    )
) else (
    echo   Frontend dependencies OK
)
findstr /i /c:"lucide-react" "%FRONTEND_DIR%\package.json" >nul 2>&1
if not errorlevel 1 (
    if not exist "%FRONTEND_DIR%\node_modules\lucide-react\package.json" (
        echo   [ERROR] lucide-react is still missing after npm install
        pause
        exit /b 1
    )
)
findstr /i /c:"framer-motion" "%FRONTEND_DIR%\package.json" >nul 2>&1
if not errorlevel 1 (
    if not exist "%FRONTEND_DIR%\node_modules\framer-motion\package.json" (
        echo   [ERROR] framer-motion is still missing after npm install
        pause
        exit /b 1
    )
)
cd /d "%ROOT%"

echo [5/7] Checking directories...
if not exist "%UPLOAD_DIR%" mkdir "%UPLOAD_DIR%"
if not exist "%KEYS_DIR%"    mkdir "%KEYS_DIR%"
if not exist "%LOG_DIR%"    mkdir "%LOG_DIR%"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"
echo   Directories OK

echo [6/7] Checking database...
cd /d "%ROOT%"
if not exist "%APP_DATA_DIR%\ops.db" (
    echo   Creating database...
    "%PYTHON%" -c "from app.db import init_db; init_db()" >nul 2>&1
    "%PYTHON%" -c "from config_manager import _init_db; _init_db()" >nul 2>&1
    echo   Database created
) else (
    echo   Database OK
)

echo [7/7] Running startup preflight...
"%PYTHON%" scripts\preflight_start_check.py --host %BACKEND_HOST% --port %BACKEND_PORT%
if errorlevel 1 (
    echo   [ERROR] Startup preflight failed
    pause
    exit /b 1
)

echo [7/7] Starting services...
start "Ops Backend v2" "%PYTHON%" -m uvicorn main:app --host %BACKEND_HOST% --port %BACKEND_PORT%
start "Ops Frontend v2" cmd /c "cd /d %FRONTEND_DIR% && call npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT%"

echo.
echo   Waiting for backend...
set READY=0
for /L %%i in (1,1,12) do (
    if !READY! equ 0 (
        "%PYTHON%" -c "import socket; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',%BACKEND_PORT%)); s.close(); exit(r)" >nul 2>&1
        if not errorlevel 1 set READY=1
    )
    if !READY! equ 0 (
        ping 127.0.0.1 -n 2 >nul
    )
)

echo.
echo   ========================================
echo     Ops Platform v2.1.6 Running
echo.
echo     Backend:  http://localhost:%BACKEND_PORT%
echo     API Docs: http://localhost:%BACKEND_PORT%/docs
echo     Frontend: http://localhost:%FRONTEND_PORT%
echo     Data:     %APP_DATA_DIR%
echo   ========================================
echo     Stop: Close the two console windows
echo   ========================================
echo.
pause

echo   Stopping...
taskkill /fi "WINDOWTITLE eq Ops Backend v2*" /F >nul 2>&1
taskkill /fi "WINDOWTITLE eq Ops Frontend v2*" /F >nul 2>&1
echo   Stopped
timeout /t 2 >nul
endlocal
