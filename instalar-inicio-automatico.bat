@echo off
title Instalar inicio automatico - SCI OCR
cd /d "%~dp0"
echo Instalando en la carpeta Inicio de Windows (sin admin)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-startup-folder.ps1"
echo.
pause
