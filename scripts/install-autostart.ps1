# Opción avanzada: tarea programada (reinicio automático si falla). Puede pedir permisos de admin.
# La opción recomendada es install-startup-folder.ps1 (usa instalar-inicio-automatico.bat).

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$RunScript = Join-Path $PSScriptRoot "run-server.ps1"
$TaskName = "SCI-OCR-Servidor"
$PythonExe = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Primero crea el entorno virtual en backend\.venv" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Servidor local SCI OCR (http://127.0.0.1:8000)" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Listo. SCI OCR quedará activo al iniciar sesión." -ForegroundColor Green
Write-Host "URL: http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "Logs: $ProjectRoot\logs\ocr-server.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Para quitar: scripts\uninstall-autostart.ps1" -ForegroundColor Yellow
Write-Host "Si falla por permisos, usa: scripts\install-startup-folder.ps1" -ForegroundColor Yellow
Write-Host "Para detener ahora: detener-servidor.bat" -ForegroundColor Yellow
Write-Host ""

Start-Sleep -Seconds 2
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/config" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
        Write-Host "Servidor respondiendo correctamente." -ForegroundColor Green
    }
} catch {
    Write-Host "El servidor está arrancando; abre http://127.0.0.1:8000 en unos segundos." -ForegroundColor Yellow
}
