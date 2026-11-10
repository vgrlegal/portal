# VGR Legal TP Robot - status (desktop icon)
$D = Join-Path $env:LOCALAPPDATA "VGRLegal\TPRobot"
$Host.UI.RawUI.WindowTitle = "VGR Legal TP Robot"
$p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*tp_worker.py*" }
if ($p) { Write-Host "VGR Legal TP Robot is RUNNING on this computer." -ForegroundColor Green } else {
  Write-Host "VGR Legal TP Robot is NOT running. Starting it..." -ForegroundColor Yellow; Start-ScheduledTask -TaskName "VGR Legal TP Robot"; Start-Sleep 10 }
Write-Host "`nLast lines of its log:"; if (Test-Path "$D\worker.log") { Get-Content "$D\worker.log" -Tail 12 }
Write-Host "`nKeep this computer ON, plugged in and on the internet. The screen may turn off."
Read-Host "`nPress Enter to close"
