#!/bin/bash
# ============================================================
# manage_bots.sh - Discord Bot Management
# Usage: bash manage_bots.sh {start|stop|restart|status|logs|update}
# ============================================================
set -euo pipefail

BOT_DIR="/home/botuser/Discord_Bots"
SERVICES=("operator-bot" "recon-bot")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

# ------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------

do_start() {
    log "Bots starten..."
    for svc in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "${svc}.service" 2>/dev/null; then
            info "${svc} laeuft bereits"
        else
            systemctl start "${svc}.service"
            log "${svc} gestartet"
        fi
    done
    sleep 2
    do_status
}

do_stop() {
    log "Bots stoppen..."
    for svc in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "${svc}.service" 2>/dev/null; then
            systemctl stop "${svc}.service"
            log "${svc} gestoppt"
        else
            info "${svc} war nicht aktiv"
        fi
    done
}

do_restart() {
    log "Bots neustarten..."
    for svc in "${SERVICES[@]}"; do
        systemctl restart "${svc}.service"
        log "${svc} neugestartet"
        sleep 2
    done
    do_status
}

do_status() {
    echo ""
    echo "=== Bot Status ==="
    echo ""
    for svc in "${SERVICES[@]}"; do
        status=$(systemctl is-active "${svc}.service" 2>/dev/null || echo "inactive")
        if [ "$status" = "active" ]; then
            echo -e "  ${svc}: ${GREEN}● aktiv${NC}"
            # Show PID and uptime
            pid=$(systemctl show -p MainPID --value "${svc}.service" 2>/dev/null)
            started=$(systemctl show -p ActiveEnterTimestamp --value "${svc}.service" 2>/dev/null)
            [ -n "$pid" ] && [ "$pid" != "0" ] && echo -e "    PID: $pid | Seit: $started"
        elif [ "$status" = "failed" ]; then
            echo -e "  ${svc}: ${RED}● fehlgeschlagen${NC}"
            echo "    Letzte Logs:"
            journalctl -u "${svc}" -n 3 --no-pager 2>/dev/null | sed 's/^/    /'
        else
            echo -e "  ${svc}: ${YELLOW}○ $status${NC}"
        fi
    done

    # Satisfactory server status
    echo ""
    sat_status=$(systemctl is-active satisfactory.service 2>/dev/null || echo "inactive")
    if [ "$sat_status" = "active" ]; then
        echo -e "  satisfactory:   ${GREEN}● aktiv${NC}"
    else
        echo -e "  satisfactory:   ${YELLOW}○ $sat_status${NC}"
    fi
    echo ""
}

do_logs() {
    local service="${2:-all}"
    local lines="${3:-50}"

    if [ "$service" = "all" ]; then
        echo "=== Operator (letzte $lines Zeilen) ==="
        journalctl -u operator-bot -n "$lines" --no-pager
        echo ""
        echo "=== Recon (letzte $lines Zeilen) ==="
        journalctl -u recon-bot -n "$lines" --no-pager
    elif [ "$service" = "gameserver" ] || [ "$service" = "gs" ]; then
        journalctl -u operator-bot -n "$lines" --no-pager
    elif [ "$service" = "monitor" ] || [ "$service" = "mon" ]; then
        journalctl -u recon-bot -n "$lines" --no-pager
    elif [ "$service" = "follow" ] || [ "$service" = "f" ]; then
        info "Live-Logs (Ctrl+C zum Beenden)..."
        journalctl -u operator-bot -u recon-bot -f
    else
        warn "Unbekannter Service: $service"
        echo "Optionen: all, gameserver|gs, monitor|mon, follow|f"
    fi
}

do_update() {
    info "Aktualisiere Python-Abhaengigkeiten..."
    sudo -u botuser "$BOT_DIR/venv/bin/pip" install --upgrade pip -q
    sudo -u botuser "$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" --upgrade -q
    log "Dependencies aktualisiert"
    echo ""
    warn "Fuer Code-Updates verwende: bash scripts/deploy.sh <tarball>"
}

do_validate() {
    info "Python-Syntax pruefen..."
    errors=0
    for f in $(find "$BOT_DIR" -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*"); do
        if ! "$BOT_DIR/venv/bin/python" -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null; then
            echo -e "  ${RED}FAIL:${NC} $f"
            errors=$((errors + 1))
        fi
    done

    if [ $errors -eq 0 ]; then
        total=$(find "$BOT_DIR" -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*" | wc -l)
        log "Alle $total Python-Dateien OK"
    else
        warn "$errors Datei(en) mit Syntaxfehlern!"
    fi
}

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

case "${1:-help}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_restart
        ;;
    status|s)
        do_status
        ;;
    logs|log|l)
        do_logs "$@"
        ;;
    update|u)
        do_update
        ;;
    validate|v)
        do_validate
        ;;
    *)
        echo "Discord Bot Management"
        echo ""
        echo "Usage: bash $0 <command>"
        echo ""
        echo "Commands:"
        echo "  start       Beide Bots starten"
        echo "  stop        Beide Bots stoppen"
        echo "  restart     Beide Bots neustarten"
        echo "  status      Status aller Services anzeigen"
        echo "  logs [svc]  Logs anzeigen (all|gameserver|monitor|follow)"
        echo "  update      Python-Dependencies aktualisieren"
        echo "  validate    Python-Syntax pruefen"
        echo ""
        ;;
esac
