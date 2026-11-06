#!/bin/bash
# VGR Legal TP worker - one-time install on Ubuntu 24.04 (run as root).
set -e
echo "== VGR TP worker install =="
apt-get update -y
apt-get install -y python3-venv python3-pip curl
mkdir -p /opt/vgr
cd /opt/vgr
curl -fsSL https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py -o tp_worker.py
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q "playwright==1.47.0" requests
./venv/bin/python -m playwright install --with-deps chromium

if [ ! -s /opt/vgr/worker.env ] || ! grep -q SUPABASE_SERVICE_KEY /opt/vgr/worker.env; then
  echo
  echo "Paste the Supabase SECRET key (Project Settings > API Keys > secret / service_role) and press Enter."
  echo "(Nothing will show while you paste.)"
  read -rs SKEY < /dev/tty
  printf 'SUPABASE_URL=https://kxngtgtgycatottekcwb.supabase.co\nSUPABASE_SERVICE_KEY=%s\n' "$SKEY" > /opt/vgr/worker.env
  chmod 600 /opt/vgr/worker.env
fi

cat > /etc/systemd/system/vgr-tp.service <<'EOF'
[Unit]
Description=VGR Legal TP worker
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/vgr
ExecStart=/opt/vgr/venv/bin/python /opt/vgr/tp_worker.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /opt/vgr/update.sh <<'EOF'
#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/vgrlegal/portal/main/worker/tp_worker.py -o /opt/vgr/tp_worker.py && systemctl restart vgr-tp && echo "TP worker updated"
EOF
chmod +x /opt/vgr/update.sh

systemctl daemon-reload
systemctl enable --now vgr-tp
sleep 5
systemctl --no-pager status vgr-tp | head -5
echo
echo "== Done. The portal should now show 'TP computer online'. =="
