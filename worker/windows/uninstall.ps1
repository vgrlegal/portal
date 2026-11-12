. "$PSScriptRoot\brand.ps1"
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
Get-TPDesk | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
