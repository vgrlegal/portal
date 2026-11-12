# Reads brand.txt (written by the installer): $NAME = the name shown on every window, $ORG = whose TPs this laptop takes.
$D = $PSScriptRoot
$NAME = "VGR Legal TP Desk"; $ORG = "vgr"
if (Test-Path "$D\brand.txt") { foreach ($l in Get-Content "$D\brand.txt") { if ($l -match "^NAME=(.+)$") { $NAME = $Matches[1].Trim() } elseif ($l -match "^ORG=(.+)$") { $ORG = $Matches[1].Trim() } } }
$TASK = $NAME
# the TP Desk's python processes from THIS install only (another brand's TP Desk on the same laptop is left alone)
function Get-TP Desk { Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*tp_worker.py*" -and (($_.CommandLine -like "*$D*") -or ($_.ExecutablePath -like "$D*")) } }
