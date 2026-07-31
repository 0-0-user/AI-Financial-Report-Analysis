@echo off
title AI Finance Analyzer Web UI
cd /d "%~dp0"

echo ========================================
echo   AI Financial Report Analyzer Web UI
echo   Starting backend service...
echo   Browser will open http://localhost:8000
echo   Close this window to stop the service
echo ========================================
echo.

rem Release port 8000 if it is occupied by an old instance
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

rem Open browser (a moment after server starts)
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

python -m uvicorn web.app:app --port 8000
pause
