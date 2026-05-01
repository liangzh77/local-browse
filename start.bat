@echo off
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python from https://python.org
    pause
    exit /b 1
)
echo Starting LocalBrowse...
cd /d "%~dp0"
python server.py %*
pause
