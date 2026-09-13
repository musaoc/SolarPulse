@echo off
title SolarPulse - Local Real-Time Solar Monitor
cls
echo =====================================================================
echo          SOLARPULSE - LOCAL REAL-TIME SOLAR MONITOR
echo =====================================================================
echo.
echo [1/3] Checking Python environment...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH! Please install Python 3.10+.
    pause
    exit /b 1
)

cd /d "%~dp0"

echo [2/3] Checking dependencies...
pip install -r requirements.txt --quiet >nul 2>&1

echo [3/3] Launching SolarPulse on http://localhost:8500 ...
echo.
echo =====================================================================
echo  - Collector TCP Port: 8899 (listening for Wi-Fi Dongle)
echo  - Web Dashboard:     http://localhost:8500
echo  - Refresh Cadence:    1 to 2 seconds (Direct local link)
echo =====================================================================
echo.
echo Opening browser in 3 seconds...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8500"

python -m uvicorn backend.app:app --host 0.0.0.0 --port 8500
pause
