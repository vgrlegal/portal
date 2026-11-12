. "$PSScriptRoot\brand.ps1"
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
Get-TP Desk | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
