# Servidor OCR en segundo plano (inicio automatico de Windows).
$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $ProjectRoot "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "ocr-server.log"

if (-not (Test-Path $PythonExe)) {
    "$(Get-Date -Format o) ERROR: Falta $PythonExe" | Add-Content -Path $LogFile -Encoding utf8
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

& (Join-Path $PSScriptRoot "wait-server.ps1") -StartIfNeeded -HiddenWindow
if ($LASTEXITCODE -ne 0) {
    "$(Get-Date -Format o) ERROR: wait-server fallo con codigo $LASTEXITCODE" | Add-Content -Path $LogFile -Encoding utf8
    exit $LASTEXITCODE
}

"$(Get-Date -Format o) Servidor listo (run-server.ps1)" | Add-Content -Path $LogFile -Encoding utf8
exit 0
