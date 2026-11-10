Unregister-ScheduledTask -TaskName "VGR Legal TP Robot" -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*tp_worker.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
