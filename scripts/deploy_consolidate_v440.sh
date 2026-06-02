#!/bin/bash
# Server-Konsolidierung v4.1.0-Hybrid -> v4.4.0
# Ausfuehren auf Server (PuTTY) als marco:  sudo bash /tmp/deploy_consolidate_v440.sh
# Bundle muss in /tmp/consolidate_v440.tar.gz liegen (55 Runtime-Files, LF).
set -euo pipefail

DIR=/home/botuser/Discord_Bots
BUNDLE=/tmp/consolidate_v440.tar.gz
TS=$(date +%s)
BACKUP=/home/botuser/backup_pre_v440_${TS}.tar.gz

[ -f "$BUNDLE" ] || { echo "FEHLER: $BUNDLE fehlt"; exit 1; }

echo "[1/6] Backup Runtime-Dirs -> $BACKUP"
sudo -u botuser tar -czf "$BACKUP" -C "$DIR" bots cogs modules utils web scripts VERSION

echo "[2/6] Extract konsolidiertes Bundle (55 Files)"
sudo -u botuser tar -xzf "$BUNDLE" -C "$DIR"

echo "[3/6] VERSION -> 4.4.0"
echo "4.4.0" | sudo -u botuser tee "$DIR/VERSION" >/dev/null

echo "[4/6] py_compile-Sanity (botuser venv)"
if ! sudo -u botuser bash -c "cd '$DIR' && ./venv/bin/python -m compileall -q bots cogs modules utils web/routes web/app.py"; then
  echo "!! COMPILE FEHLGESCHLAGEN — automatischer Rollback"
  sudo -u botuser tar -xzf "$BACKUP" -C "$DIR"
  echo "Rollback erledigt. KEIN Service-Restart durchgefuehrt."
  exit 1
fi

echo "[5/6] Service-Restart (monitor-bot zuerst)"
sudo systemctl restart monitor-bot
sleep 10
sudo systemctl restart gameserver-bot admin-bot web-dashboard

echo "[6/6] Status + Smoke-Test"
sleep 5
FAIL=0
for s in monitor-bot gameserver-bot admin-bot web-dashboard; do
  st=$(systemctl is-active "$s" || true)
  echo "  $s: $st"
  [ "$st" = "active" ] || FAIL=1
done
echo "  Dashboard-HTTP:"; curl -skI http://127.0.0.1:8080/ 2>/dev/null | head -1 || echo "  (kein 8080-Response)"

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "!! Mindestens ein Service NICHT active. Logs pruefen:"
  echo "   sudo journalctl -u <service> --since '2 min ago' --no-pager"
  echo "   Rollback: sudo -u botuser tar -xzf $BACKUP -C $DIR && sudo systemctl restart monitor-bot gameserver-bot admin-bot web-dashboard"
  exit 1
fi

echo ""
echo "OK: Server auf v4.4.0 konsolidiert. Alle Services active."
echo "Backup: $BACKUP (nach 24h Verifikation loeschbar)"
echo "Rollback bei Bedarf: sudo -u botuser tar -xzf $BACKUP -C $DIR && sudo systemctl restart monitor-bot gameserver-bot admin-bot web-dashboard"
