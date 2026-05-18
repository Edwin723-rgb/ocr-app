@echo off
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo ERROR: Falta el entorno virtual en backend\.venv
  pause
  exit /b 1
)

rem Servidor en segundo plano (si no está ya activo)
start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\run-server.ps1"

rem Esperar a que responda y abrir el navegador
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='http://127.0.0.1:8000'; $ok=$false; for ($i=0; $i -lt 40; $i++) { try { $r=Invoke-WebRequest -Uri ($url+'/config') -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { $ok=$true; break } } catch { Start-Sleep -Milliseconds 500 } }; Start-Process $url; if (-not $ok) { Write-Host 'El servidor tarda en arrancar; si la pagina no carga, espera unos segundos y recarga.' }"

exit
