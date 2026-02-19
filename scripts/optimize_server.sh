#!/bin/bash
# ============================================================
# Server-Optimierung für Netcup RS 4000 G12
# 8 Cores, 32 GB RAM - Satisfactory + Discord Bots
# Einmalig als root ausführen!
# ============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}============================================="
echo " Server-Optimierung - Netcup RS 4000 G12"
echo -e "=============================================${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Bitte als root ausfuehren: sudo bash $0${NC}"
    exit 1
fi

# ============================================================
# 1. SWAP erstellen (4GB Sicherheitsnetz)
# ============================================================
echo -e "\n${YELLOW}[1/6] Swap einrichten (4 GB)...${NC}"
if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo -e "${GREEN}  OK: 4 GB Swap erstellt${NC}"
else
    echo -e "${GREEN}  OK: Swap existiert bereits${NC}"
fi

# ============================================================
# 2. Kernel-Parameter optimieren
# ============================================================
echo -e "\n${YELLOW}[2/6] Kernel-Parameter optimieren...${NC}"

cat > /etc/sysctl.d/99-gameserver.conf << 'EOF'
# --- Swap ---
vm.swappiness=10
vm.vfs_cache_pressure=50

# --- Netzwerk (Game Server) ---
net.core.rmem_max=26214400
net.core.wmem_max=26214400
net.core.rmem_default=1048576
net.core.wmem_default=1048576
net.ipv4.udp_mem=65536 131072 262144
net.ipv4.tcp_rmem=4096 1048576 2097152
net.ipv4.tcp_wmem=4096 65536 16777216
net.ipv4.tcp_congestion_control=bbr
net.core.netdev_max_backlog=5000

# --- Verbindungen ---
net.core.somaxconn=65535
net.ipv4.tcp_max_syn_backlog=65535
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15

# --- Speicher ---
vm.dirty_ratio=10
vm.dirty_background_ratio=5
vm.overcommit_memory=1

# --- Sicherheit ---
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1
net.ipv4.icmp_echo_ignore_broadcasts=1
EOF

sysctl --system > /dev/null 2>&1
echo -e "${GREEN}  OK: Kernel-Parameter gesetzt${NC}"

# ============================================================
# 3. File Descriptor Limits erhöhen
# ============================================================
echo -e "\n${YELLOW}[3/6] File Descriptor Limits erhoehen...${NC}"

cat > /etc/security/limits.d/99-gameserver.conf << 'EOF'
# Satisfactory Server
satisfactory    soft    nofile    65535
satisfactory    hard    nofile    65535
satisfactory    soft    nproc     8192
satisfactory    hard    nproc     8192

# Bot User
botuser         soft    nofile    65535
botuser         hard    nofile    65535

# Allgemein
*               soft    nofile    65535
*               hard    nofile    65535
EOF

echo -e "${GREEN}  OK: Limits gesetzt${NC}"

# ============================================================
# 4. CPU Governor
# ============================================================
echo -e "\n${YELLOW}[4/6] CPU Governor pruefen...${NC}"

if [ -d /sys/devices/system/cpu/cpu0/cpufreq ]; then
    for cpu in /sys/devices/system/cpu/cpu[0-7]/cpufreq/scaling_governor; do
        [ -f "$cpu" ] && echo "performance" > "$cpu" 2>/dev/null || true
    done
    echo -e "${GREEN}  OK: CPU Governor auf Performance${NC}"
else
    echo -e "${GREEN}  OK: VPS - laeuft bereits auf voller Leistung${NC}"
fi

# ============================================================
# 5. I/O Scheduler optimieren
# ============================================================
echo -e "\n${YELLOW}[5/6] I/O Scheduler optimieren...${NC}"

for disk in /sys/block/sd*/queue/scheduler /sys/block/vd*/queue/scheduler; do
    if [ -f "$disk" ]; then
        echo "none" > "$disk" 2>/dev/null || echo "mq-deadline" > "$disk" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  OK: I/O Scheduler optimiert${NC}"

# ============================================================
# 6. Satisfactory Prozess-Priorität (auto via sudoers)
# ============================================================
echo -e "\n${YELLOW}[6/6] Satisfactory Prioritaet einrichten...${NC}"

# Erlaube botuser renice ohne Passwort
if ! grep -q "renice" /etc/sudoers.d/botuser 2>/dev/null; then
    echo "botuser ALL=(ALL) NOPASSWD: /usr/bin/renice" >> /etc/sudoers.d/botuser
    echo -e "${GREEN}  OK: botuser darf renice ausfuehren${NC}"
else
    echo -e "${GREEN}  OK: renice Berechtigung existiert bereits${NC}"
fi

# Setze Priorität falls Server läuft
SAT_PID=$(pgrep -f "FactoryServer" 2>/dev/null || true)
if [ -n "$SAT_PID" ]; then
    renice -10 "$SAT_PID" > /dev/null 2>&1 || true
    echo -e "${GREEN}  OK: Satisfactory Prioritaet erhoeht (PID: $SAT_PID)${NC}"
else
    echo -e "${YELLOW}  INFO: Satisfactory laeuft nicht - wird automatisch vom Bot gesetzt${NC}"
fi

# ============================================================
# Zusammenfassung
# ============================================================
echo -e "\n${GREEN}============================================="
echo " Optimierung abgeschlossen!"
echo -e "=============================================${NC}"
echo ""
echo "Aenderungen:"
echo "  ✓ 4 GB Swap als Sicherheitsnetz"
echo "  ✓ Kernel-Parameter fuer Game Server optimiert"
echo "  ✓ File Descriptor Limits erhoeht (65535)"
echo "  ✓ Netzwerk-Buffer fuer UDP vergroessert"
echo "  ✓ TCP BBR Congestion Control aktiviert"
echo "  ✓ I/O Scheduler optimiert"
echo "  ✓ Satisfactory Prozess-Prioritaet (auto via Monitor Bot)"
echo ""
echo "Der Monitor Bot setzt alle 15 Min automatisch:"
echo "  - Satisfactory Prozess-Prioritaet (renice -10)"
echo "  - Cache-Cleanup bei hohem RAM-Verbrauch"
echo "  - Temp-Dateien Bereinigung bei voller Disk"
echo ""
echo "Empfehlung: Server einmal neustarten fuer alle Aenderungen"
echo "  sudo reboot"
echo ""
