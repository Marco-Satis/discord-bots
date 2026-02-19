#!/bin/bash
# ============================================================
# deploy.sh - Deploy/Update Discord Bot Code
# Run as root or botuser on Netcup RS 4000 G12
# Usage: bash deploy.sh [tarball_path]
# ============================================================
set -euo pipefail

BOT_USER="botuser"
BOT_DIR="/home/${BOT_USER}/Discord_Bots"
BACKUP_DIR="/home/${BOT_USER}/bot_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

TARBALL="${1:-}"

# ------------------------------------------------------------------
# Pre-checks
# ------------------------------------------------------------------
if [ ! -d "$BOT_DIR" ]; then
    err "Bot-Verzeichnis $BOT_DIR existiert nicht! Bitte zuerst setup_server.sh ausfuehren."
fi

echo ""
log "=== Discord Bot Deployment ==="
info "Ziel: $BOT_DIR"
info "Zeitstempel: $TIMESTAMP"
echo ""

# ------------------------------------------------------------------
# 1. Stop Bots
# ------------------------------------------------------------------
log "1/6 Bots stoppen..."

if systemctl is-active --quiet gameserver-bot.service 2>/dev/null; then
    systemctl stop gameserver-bot.service
    log "GameServer Bot gestoppt"
else
    info "GameServer Bot war nicht aktiv"
fi

if systemctl is-active --quiet monitor-bot.service 2>/dev/null; then
    systemctl stop monitor-bot.service
    log "Monitor Bot gestoppt"
else
    info "Monitor Bot war nicht aktiv"
fi

sleep 2

# ------------------------------------------------------------------
# 2. Backup Current Code
# ------------------------------------------------------------------
log "2/6 Aktuellen Code sichern..."

mkdir -p "$BACKUP_DIR"

# Only backup code files, not data/logs/backups
if [ -d "$BOT_DIR/bots" ]; then
    tar czf "${BACKUP_DIR}/bots_pre_deploy_${TIMESTAMP}.tar.gz" \
        -C "$BOT_DIR" \
        --exclude="data" \
        --exclude="logs" \
        --exclude="backups" \
        --exclude="venv" \
        --exclude="__pycache__" \
        --exclude="*.pyc" \
        --exclude="config/.env" \
        bots/ cogs/ modules/ utils/ config/ scripts/ systemd/ \
        requirements.txt 2>/dev/null || true
    log "Backup: ${BACKUP_DIR}/bots_pre_deploy_${TIMESTAMP}.tar.gz"
else
    info "Kein vorhandener Code zum Sichern"
fi

# Keep only last 5 backups
ls -t "${BACKUP_DIR}"/bots_pre_deploy_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true

# ------------------------------------------------------------------
# 3. Deploy New Code
# ------------------------------------------------------------------
log "3/6 Neuen Code deployen..."

if [ -n "$TARBALL" ] && [ -f "$TARBALL" ]; then
    # Deploy from tarball
    info "Quelle: $TARBALL"

    # Extract to temp dir first
    TEMP_DIR=$(mktemp -d)
    tar xzf "$TARBALL" -C "$TEMP_DIR"

    # Find the actual content directory
    CONTENT_DIR="$TEMP_DIR"
    if [ -d "$TEMP_DIR/Discord_Bots" ]; then
        CONTENT_DIR="$TEMP_DIR/Discord_Bots"
    fi

    # Copy code files (preserve data, logs, backups, venv, .env)
    for dir in bots cogs modules utils scripts systemd; do
        if [ -d "$CONTENT_DIR/$dir" ]; then
            rm -rf "$BOT_DIR/$dir"
            cp -r "$CONTENT_DIR/$dir" "$BOT_DIR/$dir"
        fi
    done

    # Copy config files (but NOT .env - that stays)
    [ -f "$CONTENT_DIR/config/config.json" ] && cp "$CONTENT_DIR/config/config.json" "$BOT_DIR/config/"
    [ -f "$CONTENT_DIR/config/.env.example" ] && cp "$CONTENT_DIR/config/.env.example" "$BOT_DIR/config/"
    [ -f "$CONTENT_DIR/requirements.txt" ] && cp "$CONTENT_DIR/requirements.txt" "$BOT_DIR/"
    [ -f "$CONTENT_DIR/.gitignore" ] && cp "$CONTENT_DIR/.gitignore" "$BOT_DIR/"
    [ -f "$CONTENT_DIR/README.md" ] && cp "$CONTENT_DIR/README.md" "$BOT_DIR/"

    rm -rf "$TEMP_DIR"
    log "Code aus Tarball deployed"
else
    # No tarball provided - assume files are already in place
    if [ -n "$TARBALL" ]; then
        warn "Tarball '$TARBALL' nicht gefunden!"
    fi
    info "Kein Tarball angegeben - ueberspringe Code-Kopie"
fi

# ------------------------------------------------------------------
# 4. Fix Permissions
# ------------------------------------------------------------------
log "4/6 Berechtigungen setzen..."

chown -R "$BOT_USER:$BOT_USER" "$BOT_DIR"

# Make scripts executable
chmod +x "$BOT_DIR"/scripts/*.sh 2>/dev/null || true

# Protect .env
[ -f "$BOT_DIR/config/.env" ] && chmod 600 "$BOT_DIR/config/.env"

# Data dirs writable
for d in data logs backups; do
    mkdir -p "$BOT_DIR/$d"
    chmod 755 "$BOT_DIR/$d"
done

log "Berechtigungen gesetzt"

# ------------------------------------------------------------------
# 5. Update Dependencies
# ------------------------------------------------------------------
log "5/6 Python-Abhaengigkeiten aktualisieren..."

if [ -d "$BOT_DIR/venv" ]; then
    sudo -u "$BOT_USER" "$BOT_DIR/venv/bin/pip" install --upgrade pip -q 2>/dev/null
    sudo -u "$BOT_USER" "$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" -q 2>/dev/null
    log "Dependencies aktualisiert"
else
    warn "venv nicht gefunden! Bitte setup_server.sh ausfuehren."
fi

# ------------------------------------------------------------------
# 6. Start Bots
# ------------------------------------------------------------------
log "6/6 Bots starten..."

# Check if .env exists
if [ ! -f "$BOT_DIR/config/.env" ]; then
    warn ".env nicht konfiguriert! Bots werden NICHT gestartet."
    warn "Bitte konfigurieren: nano $BOT_DIR/config/.env"
    echo ""
    exit 0
fi

systemctl daemon-reload
systemctl start gameserver-bot.service
sleep 3
systemctl start monitor-bot.service
sleep 2

# ------------------------------------------------------------------
# Status Check
# ------------------------------------------------------------------
echo ""
log "=== Deployment Status ==="

GS_STATUS=$(systemctl is-active gameserver-bot.service 2>/dev/null || echo "inactive")
MON_STATUS=$(systemctl is-active monitor-bot.service 2>/dev/null || echo "inactive")

if [ "$GS_STATUS" = "active" ]; then
    echo -e "  GameServer Bot: ${GREEN}● aktiv${NC}"
else
    echo -e "  GameServer Bot: ${RED}● $GS_STATUS${NC}"
fi

if [ "$MON_STATUS" = "active" ]; then
    echo -e "  Monitor Bot:    ${GREEN}● aktiv${NC}"
else
    echo -e "  Monitor Bot:    ${RED}● $MON_STATUS${NC}"
fi

echo ""

if [ "$GS_STATUS" = "active" ] && [ "$MON_STATUS" = "active" ]; then
    log "Deployment erfolgreich! ✓"
else
    warn "Mindestens ein Bot nicht aktiv. Logs pruefen:"
    echo "  journalctl -u gameserver-bot -n 20 --no-pager"
    echo "  journalctl -u monitor-bot -n 20 --no-pager"
fi

echo ""
