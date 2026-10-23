@echo off
chcp 65001 >nul
schtasks /query /tn "WeiwolianghuaBackend" >nul 2>&1
if errorlevel 1 schtasks /create /tn "WeiwolianghuaBackend" /tr "%~dp0backend\.venv\Scripts\pythonw.exe %~dp0backend\scriptsackend_watchdog.py" /sc onlogon /f >nul 2>&1
schtasks /run /tn "WeiwolianghuaBackend" >nul 2>&1
echo [1/2] Backend started (silent, auto-restart, auto-start at login)
echo [2/2] Starting frontend...
cd /d "%~dp0frontend"
start "Weiwolianghua Frontend" cmd /k "for /l %%i in () do (npm run dev & ping -n 4 127.0.0.1 >nul)"
echo.
echo   Frontend: http://localhost:5173
echo   Close the frontend window to stop it; backend keeps running silently.
pause
