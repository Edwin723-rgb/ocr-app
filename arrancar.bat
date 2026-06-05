@echo off
chcp 65001 >nul
title SCI OCR - Arrancar
cd /d "%~dp0"

echo.
echo  SCI OCR - Iniciando...
echo.

if not exist "backend\.venv\Scripts\python.exe" (
  echo  Falta el entorno virtual.
  echo  Ejecuta primero: instalar.bat
  echo.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\wait-server.ps1" -StartIfNeeded
if errorlevel 1 (
  echo.
  echo  No se pudo iniciar el servidor.
  echo  Revisa la ventana del servidor o el archivo logs\ocr-server.log
  echo.
  pause
  exit /b 1
)

set "APP_URL=http://127.0.0.1:8000/"
if not "%OCR_PORT%"=="" set "APP_URL=http://127.0.0.1:%OCR_PORT%/"

start "" "%APP_URL%"
echo  Servidor listo: %APP_URL%
echo  Dejalo abierto la ventana "SCI OCR" con los logs.
echo  Para detener: detener-servidor.bat
echo.
ping 127.0.0.1 -n 5 >nul
exit /b 0
