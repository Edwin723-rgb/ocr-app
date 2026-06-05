@echo off
chcp 65001 >nul
title Instalar inicio automatico - SCI OCR
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo  Falta el entorno virtual. Ejecuta primero: instalar.bat
  pause
  exit /b 1
)

echo  Instalando acceso directo en Inicio de Windows...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-startup-folder.ps1"
echo.
pause
exit /b 0
