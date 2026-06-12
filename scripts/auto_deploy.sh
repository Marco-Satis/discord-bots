#!/bin/bash
# ============================================================
# auto_deploy.sh — Manuelles Deployment fuer Discord-Bot-Projekt
#
# Fuehrt git pull, pip install und Service-Restart aus.
# Kann als Fallback genutzt werden, wenn der GitHub-Webhook
# (F47: webhook_route.py) nicht verfuegbar ist.
#
# Ausfuehren: bash /home/botuser/Discord_Bots/scripts/auto_deploy.sh
# ============================================================
set -euo pipefail

BOT_DIR="/home/botuser/Discord_Bots"
VENV_DIR="${BOT_DIR}/venv"
SERVICES="marshal-bot recon-bot operator-bot web-dashboard"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; exit 1; }

# --- Voraussetzungen pruefen ---
[ -d "$BOT_DIR" ] || err "Bot-Verzeichnis $BOT_DIR nicht gefunden!"
[ -d "$VENV_DIR" ] || err "Virtual Environment $VENV_DIR nicht gefunden!"

echo ""
log "=== Auto-Deploy gestartet ==="
echo ""

# --- 1. Code aktualisieren ---
log "1/3 Git Pull..."
cd "$BOT_DIR"
git pull origin main
log "Code aktualisiert"

# --- 2. Abhaengigkeiten installieren ---
log "2/3 Abhaengigkeiten installieren..."
source "${VENV_DIR}/bin/activate"
pip install -r requirements.txt -q
log "Abhaengigkeiten aktualisiert"

# --- 3. Services neustarten ---
log "3/3 Services neustarten..."
sudo systemctl restart $SERVICES
log "Services neugestartet"

# --- Status pruefen ---
echo ""
log "=== Deploy-Status ==="
ALL_OK=true
for svc in $SERVICES; do
    STATUS=$(systemctl is-active "${svc}.service" 2>/dev/null || echo "inactive")
    if [ "$STATUS" = "active" ]; then
        echo -e "  ${svc}: ${GREEN}aktiv${NC}"
    else
        echo -e "  ${svc}: ${RED}${STATUS}${NC}"
        ALL_OK=false
    fi
done

echo ""
if [ "$ALL_OK" = true ]; then
    log "Deploy erfolgreich abgeschlossen"
else
    warn "Mindestens ein Service nicht aktiv. Logs pruefen:"
    warn "  journalctl -u <service-name> -n 20 --no-pager"
fi
echo ""
