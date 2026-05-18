$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupDir "SCI OCR.lnk"
if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
    Write-Host "Acceso directo eliminado de Inicio." -ForegroundColor Green
} else {
    Write-Host "No habia acceso directo en Inicio." -ForegroundColor Yellow
}
