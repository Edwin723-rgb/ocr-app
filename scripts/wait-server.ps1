param(
    [switch]$StartIfNeeded,
    [switch]$HiddenWindow,
    [int]$MaxAttempts = 50,
    [int]$DelayMs = 500
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $ProjectRoot "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "ocr-server.log"

$port = if ($env:OCR_PORT) { [int]$env:OCR_PORT } else { 8000 }
$hostName = if ($env:OCR_HOST) { $env:OCR_HOST } else { "127.0.0.1" }
$url = "http://${hostName}:$port/config"

function Test-OcrServer {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-ListenerPid {
    param([int]$ListenPort)

    try {
        $connection = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($connection) {
            return [int]$connection.OwningProcess
        }
    } catch {
        # Fallback sin modulo NetTCPIP (algunas ediciones de Windows).
    }

    $pattern = ":\s*$ListenPort\s"
    $lines = netstat -ano -p tcp | Select-String "LISTENING" | Select-String $pattern
    foreach ($line in $lines) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ -ne "" }
        if ($parts.Count -ge 1) {
            $pidText = $parts[-1]
            if ($pidText -match '^\d+$') {
                return [int]$pidText
            }
        }
    }

    return $null
}

function Start-OcrServerProcess {
    if (-not (Test-Path $PythonExe)) {
        Write-Error "No se encontro $PythonExe. Ejecuta instalar.bat primero."
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $existingPid = Get-ListenerPid -ListenPort $port
    if ($existingPid) {
        if (Test-OcrServer) {
            return
        }
        Write-Host "Puerto $port ocupado por PID $existingPid sin respuesta. Reiniciando..."
        Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    if ($HiddenWindow) {
        $env:OCR_HOST = $hostName
        $env:OCR_PORT = "$port"
        $env:OCR_RELOAD = "0"
        Set-Location $BackendDir
        "$(Get-Date -Format o) Iniciando SCI OCR en $url (oculto)" | Add-Content -Path $LogFile -Encoding utf8
        Start-Process -FilePath $PythonExe -ArgumentList "main.py" -WorkingDirectory $BackendDir -WindowStyle Hidden
    } else {
        $serverCmd = "cd /d `"$BackendDir`" && set OCR_HOST=$hostName&& set OCR_PORT=$port&& set OCR_RELOAD=0&& `"$PythonExe`" main.py"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $serverCmd -WorkingDirectory $BackendDir -WindowStyle Normal
        "$(Get-Date -Format o) Lanzado desde wait-server.ps1 (ventana visible)" | Add-Content -Path $LogFile -Encoding utf8
    }
}

if (Test-OcrServer) {
    exit 0
}

if (-not $StartIfNeeded) {
    Write-Host "El servidor no esta activo en $url"
    exit 1
}

Start-OcrServerProcess

for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
    if (Test-OcrServer) {
        exit 0
    }
    Start-Sleep -Milliseconds $DelayMs
}

Write-Host "El servidor no respondio a tiempo. Revisa la ventana SCI OCR o $LogFile"
exit 1
