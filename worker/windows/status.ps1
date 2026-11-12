# TP Desk - status (desktop icon)
. "$PSScriptRoot\brand.ps1"
$Host.UI.RawUI.WindowTitle = $NAME
Write-Host "  $NAME  " -ForegroundColor White -BackgroundColor DarkBlue
Write-Host "  Computer: $env:COMPUTERNAME"
$p = Get-TP Desk
if ($p) { Write-Host "`nRUNNING on this computer." -ForegroundColor Green } else {
  Write-Host "`nNOT running. Starting it..." -ForegroundColor Yellow; Start-ScheduledTask -TaskName $TASK; Start-Sleep 10 }
Write-Host "`nLast lines of its log:"; if (Test-Path "$D\worker.log") { Get-Content "$D\worker.log" -Tail 12 }
Write-Host "`nKeep this computer ON, plugged in and on the internet. The screen may turn off."
Read-Host "`nPress Enter to close"
