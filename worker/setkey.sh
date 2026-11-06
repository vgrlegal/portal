#!/bin/bash
# Re-enter the Supabase secret key for the TP worker (the key is never shown).
read -rsp "Paste the Supabase secret key (sb_secret_...) and press Enter: " K < /dev/tty; echo
K="$(echo -n "$K" | tr -d '[:space:]')"
case "$K" in sb_secret_*|eyJ*) ;; *) echo "That does not look like a Supabase secret key (should start with sb_secret_). Nothing saved."; exit 1;; esac
printf 'SUPABASE_URL=https://kxngtgtgycatottekcwb.supabase.co\nSUPABASE_SERVICE_KEY=%s\n' "$K" > /opt/vgr/worker.env
chmod 600 /opt/vgr/worker.env
systemctl restart vgr-tp; sleep 6
if journalctl -u vgr-tp -n 3 --no-pager | grep -q 401; then echo "Key saved, but Supabase still says Unauthorized. Copy the key again with the copy button and rerun."; else echo "Key OK - TP computer online."; fi
