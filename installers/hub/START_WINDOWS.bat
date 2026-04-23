@echo off
title GestureLink Hub
chcp 65001 >nul

echo.
echo   ╔══════════════════════════════════════╗
echo   ║        GestureLink Hub               ║
echo   ╚══════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: Python is not installed or not in PATH.
    echo.
    echo   Please download Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: During install, check "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:: Check Python version is 3.10+
python -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: Python 3.10 or newer required.
    echo   Your version:
    python --version
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo   Using: %%i
echo.

:: Run the smart installer
python "%~dp0install.py"
if %errorlevel% neq 0 (
    echo.
    echo   Something went wrong. Check the error above.
    pause
)
