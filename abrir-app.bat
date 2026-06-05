@echo off
chcp 65001 >nul
title SCI OCR - Abrir app
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo  Falta el entorno virtual. Ejecuta: instalar.bat
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\wait-server.ps1" -StartIfNeeded
if errorlevel 1 (
  echo  No se pudo conectar al servidor. Prueba arrancar.bat
  pause
  exit /b 1
)

set "APP_URL=http://127.0.0.1:8000/"
if not "%OCR_PORT%"=="" set "APP_URL=http://127.0.0.1:%OCR_PORT%/"

start "" "%APP_URL%"
echo  Abierto: %APP_URL%
ping 127.0.0.1 -n 3 >nul
exit /b 0
