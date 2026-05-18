$TaskName = "SCI-OCR-Servidor"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Inicio automático desactivado ($TaskName)." -ForegroundColor Green
Write-Host "El servidor sigue corriendo hasta que ejecutes detener-servidor.bat" -ForegroundColor Yellow
