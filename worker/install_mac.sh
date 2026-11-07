#!/bin/bash
# VGR Legal TP worker on a Mac (runs in the background, starts at login).
set -e
D="$HOME/VGR_TP"; mkdir -p "$D"; cd "$D"
echo "== VGR TP worker (Mac) =="
curl -fsSL https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py -o tp_worker.py
PY="$HOME/Documents/VGR_Helper/venv/bin/python"
if [ ! -x "$PY" ]; then
  command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv venv --python 3.12 "$D/venv"; PY="$D/venv/bin/python"
  uv pip install --python "$PY" playwright requests
  "$PY" -m playwright install chromium
else
  "$PY" -m pip install -q requests 2>/dev/null || "$(dirname "$PY")/pip" install -q requests
  "$PY" -c "import playwright" 2>/dev/null || { "$PY" -m pip install -q playwright; "$PY" -m playwright install chromium; }
fi
if [ ! -s "$D/worker.env" ]; then
  read -rsp "Paste the Supabase secret key (sb_secret_...) and press Enter: " K < /dev/tty; echo
  K="$(echo -n "$K" | tr -d '[:space:]')"
  case "$K" in sb_secret_*) K="sb_secret_$(echo -n "${K#sb_secret_}" | awk -F'sb_secret_' '{print $1}')";; esac
  printf 'SUPABASE_URL=https://kxngtgtgycatottekcwb.supabase.co\nSUPABASE_SERVICE_KEY=%s\n' "$K" > "$D/worker.env"; chmod 600 "$D/worker.env"
fi
PL="$HOME/Library/LaunchAgents/in.vgrlegal.tp.plist"; mkdir -p "$(dirname "$PL")"
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>in.vgrlegal.tp</string>
<key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>$PY</string><string>$D/tp_worker.py</string></array>
<key>WorkingDirectory</key><string>$D</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$D/worker.log</string><key>StandardErrorPath</key><string>$D/worker.log</string>
</dict></plist>
PLIST
launchctl unload "$PL" 2>/dev/null || true
launchctl load "$PL"
sleep 20
tail -n 3 "$D/worker.log"
if grep -q "tnreginet.gov.in/portal: 200" "$D/worker.log"; then echo "== Done: TP computer online (this Mac). =="; else echo "== Started. If the line above shows 401, the key is wrong: run  rm ~/VGR_TP/worker.env  and this command again. =="; fi
