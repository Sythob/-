@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
start "Weiwolianghua Backend" cmd /k "for /l %%i in () do (.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 & ping -n 4 127.0.0.1 >nul)"
