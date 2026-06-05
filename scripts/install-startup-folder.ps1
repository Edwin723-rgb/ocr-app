# Coloca un acceso directo en Inicio de Windows (no requiere administrador).
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$RunScript = Join-Path $PSScriptRoot "run-server.ps1"
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "SCI OCR.lnk"
$PythonExe = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Falta backend\.venv. Ejecuta instalar.bat en la raiz del proyecto." -ForegroundColor Red
    exit 1
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`""
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Servidor SCI OCR"
$shortcut.Save()

& $RunScript

Write-Host ""
Write-Host "Listo. SCI OCR arrancara al iniciar sesion (carpeta Inicio)." -ForegroundColor Green
Write-Host "Acceso directo: $ShortcutPath" -ForegroundColor DarkGray
Write-Host "App: http://127.0.0.1:8000  |  Arrancar: arrancar.bat  |  Abrir: abrir-app.bat" -ForegroundColor Cyan
Write-Host "Quitar: scripts\uninstall-startup-folder.ps1" -ForegroundColor Yellow
Write-Host ""
