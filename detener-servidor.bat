@echo off
title Detener SCI OCR
set PORT=8000
if not "%OCR_PORT%"=="" set PORT=%OCR_PORT%

echo Deteniendo servidor en puerto %PORT%...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p=%PORT%; Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; if ($?) { Write-Host 'Servidor detenido.' } else { Write-Host 'No habia servidor activo en el puerto' $p }"

pause
