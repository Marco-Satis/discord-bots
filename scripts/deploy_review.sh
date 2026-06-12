#!/bin/bash
# Deploy-Script fuer Review-Fixes
# Ausfuehren auf dem Server als User mit sudo-Rechten

set -e

BOTDIR="/home/botuser/Discord_Bots"

echo "=== Backup ==="
sudo -u botuser cp $BOTDIR/web/app.py $BOTDIR/web/app.py.bak
sudo -u botuser cp $BOTDIR/web/auth.py $BOTDIR/web/auth.py.bak
sudo -u botuser cp $BOTDIR/modules/satisfactory/server.py $BOTDIR/modules/satisfactory/server.py.bak
sudo -u botuser cp $BOTDIR/modules/monitoring/health_check.py $BOTDIR/modules/monitoring/health_check.py.bak
sudo -u botuser cp $BOTDIR/modules/monitoring/login_audit.py $BOTDIR/modules/monitoring/login_audit.py.bak

echo "=== Deploy ==="
sudo cp /tmp/app.py $BOTDIR/web/app.py
sudo cp /tmp/auth.py $BOTDIR/web/auth.py
sudo cp /tmp/sat_server.py $BOTDIR/modules/satisfactory/server.py
sudo cp /tmp/health_check.py $BOTDIR/modules/monitoring/health_check.py
sudo cp /tmp/login_audit.py $BOTDIR/modules/monitoring/login_audit.py
sudo chown botuser:botuser $BOTDIR/web/app.py $BOTDIR/web/auth.py $BOTDIR/modules/satisfactory/server.py $BOTDIR/modules/monitoring/health_check.py $BOTDIR/modules/monitoring/login_audit.py
sudo rm -f $BOTDIR/modules/monitoring/status_writer.py.tmp.5.1773603060648

echo "=== Restart ==="
sudo systemctl restart web-dashboard
sudo systemctl restart recon-bot
sleep 10

echo "=== Logs pruefen ==="
sudo journalctl -u web-dashboard --since '30 sec ago' --no-pager | grep -iE 'error|exception|traceback' || echo "web-dashboard: 0 Fehler"
sudo journalctl -u recon-bot --since '30 sec ago' --no-pager | grep -iE 'error|exception|traceback' || echo "recon-bot: 0 Fehler"

echo "=== Services ==="
for s in recon-bot operator-bot marshal-bot web-dashboard satisfactory minecraft-bmc; do
    echo "$s: $(systemctl is-active $s)"
done

echo "=== DEPLOY FERTIG ==="
