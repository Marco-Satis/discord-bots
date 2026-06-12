#!/bin/bash
# Bot Watchdog — Phase 9a
# Checks if Discord bots are running and sends email alert if not.
# Runs via systemd timer every 5 minutes, independent of bots.

SERVICES=("operator-bot.service" "recon-bot.service")
EMAIL_TO="${BOT_WATCHDOG_EMAIL:-}"
HOSTNAME=$(hostname)
LOG_FILE="/var/log/bot-watchdog.log"

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

send_alert() {
    local service="$1"
    local status="$2"
    
    if [ -z "$EMAIL_TO" ]; then
        log_msg "WARNUNG: Kein EMAIL_TO gesetzt, kann Alert nicht senden"
        return
    fi
    
    subject="⚠️ Bot-Watchdog: $service ausgefallen auf $HOSTNAME"
    body="Der Service $service ist nicht aktiv.\n\nStatus:\n$status\n\nZeit: $(date)\nServer: $HOSTNAME\n\nAutomatischer Neustart wird versucht..."
    
    echo -e "$body" | mail -s "$subject" "$EMAIL_TO" 2>/dev/null
    log_msg "Alert gesendet an $EMAIL_TO fuer $service"
}

restart_service() {
    local service="$1"
    log_msg "Starte $service neu..."
    systemctl restart "$service" 2>/dev/null
    sleep 5
    
    if systemctl is-active --quiet "$service"; then
        log_msg "$service erfolgreich neugestartet"
        return 0
    else
        log_msg "FEHLER: $service konnte nicht neugestartet werden!"
        return 1
    fi
}

# Main check
for service in "${SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$service"; then
        status=$(systemctl status "$service" 2>&1 | head -20)
        log_msg "ALARM: $service ist nicht aktiv!"
        
        # Try restart
        if restart_service "$service"; then
            log_msg "$service wurde automatisch neugestartet"
        else
            # Restart failed - send alert
            send_alert "$service" "$status"
        fi
    fi
done
