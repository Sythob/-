@echo off
chcp 65001 >nul
cd /d "%~dp0frontend"
start "Weiwolianghua Frontend" cmd /k "for /l %%i in () do (npm run dev & ping -n 4 127.0.0.1 >nul)"
