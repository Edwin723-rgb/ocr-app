@echo off
chcp 65001 >nul
title SCI OCR - Detener
cd /d "%~dp0"

echo.
echo  Deteniendo servidor OCR...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-server.ps1"
echo.
pause
exit /b 0
