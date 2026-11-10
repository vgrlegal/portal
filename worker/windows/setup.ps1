# VGR Legal TP Robot - Windows setup (run by the installer; safe to run again).
# Installs Python + the robot + its browser, asks once for this computer's key, starts the robot at every sign-in,
# keeps the computer awake, and puts a "VGR Legal TP Robot" icon on the desktop.
$ErrorActionPreference = "Stop"
$D = Join-Path $env:LOCALAPPDATA "VGRLegal\TPRobot"
New-Item -ItemType Directory -Force -Path $D | Out-Null
Set-Location $D
$Host.UI.RawUI.WindowTitle = "VGR Legal TP Robot - Setup"
function Say($m) { Write-Host ""; Write-Host "== $m ==" -ForegroundColor Cyan }

Say "1/6 Downloading the robot"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py" -OutFile "$D\tp_worker.py"

Say "2/6 Installing Python (first time only)"
$uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (-not (Test-Path $uv)) { powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" }
if (-not (Test-Path $uv)) { $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source }
if (-not $uv) { throw "Could not install Python (uv). Check the internet and run setup again." }
$PY = Join-Path $D "venv\Scripts\python.exe"
if (-not (Test-Path $PY)) { & $uv venv --python 3.12 "$D\venv" }

Say "3/6 Installing the robot's parts and its browser (a few minutes)"
& $uv pip install --python $PY playwright requests
& $PY -m playwright install chromium

Say "4/6 This computer's key"
$envf = Join-Path $D "worker.env"
if (-not (Test-Path $envf) -or (Get-Item $envf).Length -lt 20) {
  Write-Host "Paste the key given by VGR Legal for THIS computer (it starts with sb_secret_) and press Enter."
  Write-Host "Nothing shows while you paste; that is normal."
  $s = Read-Host -AsSecureString "Key"
  $k = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()
  if (-not $k.StartsWith("sb_secret_")) { throw "That does not look like the key (it must start with sb_secret_). Run setup again." }
  [IO.File]::WriteAllText($envf, "SUPABASE_URL=https://kxngtgtgycatottekcwb.supabase.co`nSUPABASE_SERVICE_KEY=$k`n")
  icacls $envf /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
}

Say "5/6 Start at every sign-in, and stay awake"
$run = Join-Path $D "run.cmd"
@"
@echo off
rem VGR Legal TP Robot: runs the robot and starts it again after each self-update.
cd /d "$D"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
:loop
"$PY" tp_worker.py >> worker.log 2>&1
timeout /t 5 /nobreak >nul
goto loop
"@ | Set-Content -Encoding ASCII $run
$vbs = Join-Path $D "run-hidden.vbs"
"CreateObject(""WScript.Shell"").Run """"""$run"""""", 0, False" | Set-Content -Encoding ASCII $vbs
$act = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "VGR Legal TP Robot" -Action $act -Trigger $trg -Settings $set -Description "VGR Legal TP Robot (TNREGINET)" -Force | Out-Null
# never sleep or hibernate (the screen may still turn off)
powercfg /change standby-timeout-ac 0; powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0; powercfg /change hibernate-timeout-dc 0
# closing the lid does nothing (plugged in or on battery)
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 2>$null; powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 2>$null; powercfg /setactive SCHEME_CURRENT 2>$null

Say "6/6 Starting the robot"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*tp_worker.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName "VGR Legal TP Robot"
Start-Sleep -Seconds 25
if (Test-Path "$D\worker.log") { Get-Content "$D\worker.log" -Tail 4 }
if ((Test-Path "$D\worker.log") -and (Select-String -Path "$D\worker.log" -Pattern "tnreginet.gov.in/portal: 200" -Quiet)) {
  Write-Host "`nDone. This computer is now a TP computer. The portal shows 'TP Computer Ready'." -ForegroundColor Green
} else {
  Write-Host "`nStarted. If the portal does not show 'TP Computer Ready' in 2 minutes, open the desktop icon and send a photo of it to VGR Legal." -ForegroundColor Yellow
}
Read-Host "Press Enter to close"
