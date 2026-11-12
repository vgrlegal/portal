. "$PSScriptRoot\brand.ps1"
Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
Get-Robot | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
