@echo off
chcp 65001 >nul
title Instalar SCI OCR
cd /d "%~dp0"

echo.
echo  === Instalacion local SCI OCR ===
echo.

where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
  goto :have_python
)

where python >nul 2>&1
if %errorlevel%==0 (
  set "PY=python"
  goto :have_python
)

echo  ERROR: No se encontro Python.
echo  Instala Python 3.11+ desde https://www.python.org/downloads/
echo  Marca "Add python to PATH" en el instalador.
echo.
pause
exit /b 1

:have_python
if not exist "backend" (
  echo  ERROR: Falta la carpeta backend en %~dp0
  pause
  exit /b 1
)

if not exist "backend\.venv\Scripts\python.exe" (
  echo  Creando entorno virtual en backend\.venv ...
  %PY% -m venv "backend\.venv"
  if errorlevel 1 (
    echo  ERROR al crear el entorno virtual.
    pause
    exit /b 1
  )
)

echo  Instalando dependencias (puede tardar unos minutos)...
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
"backend\.venv\Scripts\pip.exe" install -r "backend\requirements.txt"
if errorlevel 1 (
  echo.
  echo  ERROR al instalar dependencias.
  pause
  exit /b 1
)

echo.
echo  Listo. Ahora puedes usar:
echo    arrancar.bat       - inicia servidor y abre el navegador
echo    abrir-app.bat      - solo abre la app (arranca si hace falta)
echo    detener-servidor.bat
echo.
pause
exit /b 0
