@echo off
setlocal enabledelayedexpansion

REM Set working directory to the project root
cd /d "%~dp0.."

echo ==========================================================
echo Starting Smart Building Management System (Windows)...
echo ==========================================================

REM Detect Python executable
set "PYTHON_CMD="
if exist "venv\Scripts\python.exe" (
    set "PYTHON_CMD=venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if !errorlevel! equ 0 (
        set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found! Please install Python or setup virtual environment.
    pause
    exit /b 1
)

REM Get local IPv4 address
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr "IPv4 Address"') do (
    set ip=%%A
    set ip=!ip: =!
)

echo.
echo ==========================================================
echo Server is starting... 
echo You can view the app from THIS computer at: 
echo http://localhost:8000
echo.
echo To access the app from ANY DEVICE on your Wi-Fi/Network:
if defined ip (
    echo http://!ip!:8000
) else (
    echo [Could not detect local IP automatically - check ipconfig]
)
echo ==========================================================
echo.
echo NOTE: If Windows Firewall asks for permission, click "Allow Access" 
echo so other devices on your network can reach the server.
echo.

if /I "%SBMS_RELOAD%"=="1" (
    "%PYTHON_CMD%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
) else (
    "%PYTHON_CMD%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
)
