param(
    [int]$Port = $(if ($env:OCR_PORT) { [int]$env:OCR_PORT } else { 8000 })
)

$stopped = $false

function Stop-ListenerOnPort {
    param([int]$ListenPort)

    $pids = @()

    try {
        $pids += Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
        # Fallback netstat
    }

    if ($pids.Count -eq 0) {
        $pattern = ":\s*$ListenPort\s"
        $lines = netstat -ano -p tcp | Select-String "LISTENING" | Select-String $pattern
        foreach ($line in $lines) {
            $parts = ($line.ToString() -split '\s+') | Where-Object { $_ -ne "" }
            if ($parts.Count -ge 1 -and $parts[-1] -match '^\d+$') {
                $pids += [int]$parts[-1]
            }
        }
    }

    foreach ($procId in ($pids | Select-Object -Unique)) {
        if ($procId -le 0) { continue }
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Proceso detenido: PID $procId ($($proc.ProcessName))"
            $script:stopped = $true
        } catch {
            Write-Host "No se pudo detener PID $procId : $($_.Exception.Message)"
        }
    }
}

Stop-ListenerOnPort -ListenPort $Port

if ($stopped) {
    Write-Host "Servidor detenido en puerto $Port."
    exit 0
}

Write-Host "No habia servidor activo en el puerto $Port."
exit 0
