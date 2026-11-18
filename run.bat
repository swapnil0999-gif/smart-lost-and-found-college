@echo off
title Smart Lost and Found - College
cd /d "%~dp0"
echo ==========================================
echo   SMART LOST AND FOUND SYSTEM - SETUP
echo ==========================================
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher was not found. Install Python 3.11 or 3.12 from python.org.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv .venv
    if errorlevel 1 py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Could not create virtual environment. Install Python 3.11 or 3.12.
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"
echo Installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installation failed. Check Python version and internet connection.
    pause
    exit /b 1
)
echo.
echo Starting application...
start "" http://127.0.0.1:5000
python app.py
pause
