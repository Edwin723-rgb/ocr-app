# Servidor OCR en segundo plano (sin ventana). Usado por el inicio automático de Windows.
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $ProjectRoot "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "ocr-server.log"

if (-not (Test-Path $PythonExe)) {
    Write-Error "No se encontró el entorno virtual en: $PythonExe"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Evitar duplicar el servidor si ya está escuchando en el puerto
$port = if ($env:OCR_PORT) { [int]$env:OCR_PORT } else { 8000 }
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($existing) {
    "$(Get-Date -Format o) Servidor ya activo en puerto $port (PID $($existing.OwningProcess))" |
        Add-Content -Path $LogFile -Encoding utf8
    exit 0
}

$env:OCR_RELOAD = "0"
if (-not $env:OCR_HOST) { $env:OCR_HOST = "127.0.0.1" }

Set-Location $BackendDir
"$(Get-Date -Format o) Iniciando SCI OCR en http://127.0.0.1:$port" | Add-Content -Path $LogFile -Encoding utf8

& $PythonExe main.py 2>&1 | ForEach-Object {
    "$(Get-Date -Format 'HH:mm:ss') $_" | Add-Content -Path $LogFile -Encoding utf8
}
