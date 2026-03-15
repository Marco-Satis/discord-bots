#!/bin/bash
# =============================================================================
# Minecraft Server Setup Script fuer Netcup RS 4000 G12
# Richtet Vanilla/Paper + Better MC ein (Multi-Server)
# Ausfuehren als: sudo bash setup_minecraft.sh
# =============================================================================

set -e

echo "========================================="
echo " Minecraft Server Setup (Multi-Server)"
echo "========================================="

# --- Konfiguration ---
MC_USER="minecraft"
MC_HOME="/home/minecraft"

VANILLA_DIR="$MC_HOME/vanilla"
BMC_DIR="$MC_HOME/bettermc"
BACKUP_DIR_VANILLA="$MC_HOME/backups/vanilla"
BACKUP_DIR_BMC="$MC_HOME/backups/bmc"

# RCON Passwoerter (AENDERN!)
RCON_PASS_VANILLA="ChangeMe_Vanilla_2026"
RCON_PASS_BMC="ChangeMe_BMC_2026"

# Paper MC Version (aktuell)
PAPER_VERSION="1.21.4"
PAPER_BUILD="209"

# Aikar's optimierte JVM-Flags (aus bestehendem minecraft.service)
AIKAR_FLAGS="-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1"

# =============================================================================
# 0. Bestehenden minecraft.service pruefen
# =============================================================================

echo ""
echo "[0/8] Bestehende Minecraft-Installation pruefen..."

if [ -f /etc/systemd/system/minecraft.service ]; then
    EXISTING_STATUS=$(systemctl is-active minecraft.service 2>/dev/null || true)
    if [ "$EXISTING_STATUS" = "active" ]; then
        echo "  WARNUNG: Alter minecraft.service ist noch aktiv!"
        echo "  Bitte erst stoppen: sudo systemctl stop minecraft.service"
        echo "  Dann disable:       sudo systemctl disable minecraft.service"
        echo ""
        read -p "  Trotzdem fortfahren? (j/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[jJyY]$ ]]; then
            echo "  Abgebrochen."
            exit 1
        fi
    fi
    echo "  INFO: Bestehender minecraft.service gefunden (Status: $EXISTING_STATUS)"
    echo "  INFO: Wird durch minecraft-vanilla.service + minecraft-bmc.service ersetzt"

    # Pruefen ob /home/minecraft/server/ existiert (alter Pfad)
    if [ -d "$MC_HOME/server" ]; then
        echo "  INFO: Existierendes Verzeichnis $MC_HOME/server/ gefunden"
        echo "  INFO: Wird als Basis fuer Vanilla/Paper verwendet (verschoben nach $VANILLA_DIR)"
    fi
else
    echo "  OK: Kein bestehender minecraft.service"
fi

# =============================================================================
# 1. Verzeichnisse erstellen + bestehende Daten migrieren
# =============================================================================

echo ""
echo "[1/8] Verzeichnisse erstellen..."

sudo -u $MC_USER mkdir -p "$MC_HOME/backups"
sudo -u $MC_USER mkdir -p "$BACKUP_DIR_VANILLA"
sudo -u $MC_USER mkdir -p "$BACKUP_DIR_BMC"
sudo -u $MC_USER mkdir -p "$BMC_DIR"

# Wenn /home/minecraft/server existiert → nach vanilla verschieben
if [ -d "$MC_HOME/server" ] && [ ! -d "$VANILLA_DIR" ]; then
    echo "  Migration: $MC_HOME/server → $VANILLA_DIR"
    sudo -u $MC_USER mv "$MC_HOME/server" "$VANILLA_DIR"
    echo "  OK: Bestehende Installation nach $VANILLA_DIR verschoben"
elif [ ! -d "$VANILLA_DIR" ]; then
    sudo -u $MC_USER mkdir -p "$VANILLA_DIR"
    echo "  OK: $VANILLA_DIR erstellt"
else
    echo "  SKIP: $VANILLA_DIR existiert bereits"
fi

echo "  OK: $BMC_DIR"
echo "  OK: Backup-Verzeichnisse"

# =============================================================================
# 2. Paper MC (Vanilla/Paper) herunterladen
# =============================================================================

echo ""
echo "[2/8] Paper MC ${PAPER_VERSION} Build ${PAPER_BUILD} herunterladen..."

PAPER_URL="https://api.papermc.io/v2/projects/paper/versions/${PAPER_VERSION}/builds/${PAPER_BUILD}/downloads/paper-${PAPER_VERSION}-${PAPER_BUILD}.jar"
PAPER_JAR="$VANILLA_DIR/server.jar"

if [ ! -f "$PAPER_JAR" ]; then
    sudo -u $MC_USER wget -q -O "$PAPER_JAR" "$PAPER_URL"
    echo "  OK: Paper MC heruntergeladen"
else
    echo "  SKIP: server.jar existiert bereits"
    # Pruefen ob es ein Paper-JAR ist
    echo "  INFO: $(sudo -u $MC_USER java -jar $PAPER_JAR --version 2>/dev/null | head -1 || echo 'Version nicht lesbar')"
fi

# EULA akzeptieren
sudo -u $MC_USER bash -c "echo 'eula=true' > $VANILLA_DIR/eula.txt"
echo "  OK: EULA akzeptiert"

# =============================================================================
# 3. Vanilla/Paper server.properties
# =============================================================================

echo ""
echo "[3/8] Vanilla/Paper server.properties konfigurieren..."

if [ -f "$VANILLA_DIR/server.properties" ]; then
    # Bestehende Properties: nur RCON sicherstellen
    if grep -q "enable-rcon=true" "$VANILLA_DIR/server.properties"; then
        echo "  OK: RCON ist bereits aktiviert"
    else
        echo "  Aktiviere RCON in bestehender server.properties..."
        # RCON-Eintraege hinzufuegen/aktualisieren
        sudo -u $MC_USER sed -i '/^enable-rcon=/d; /^rcon\.port=/d; /^rcon\.password=/d' "$VANILLA_DIR/server.properties"
        sudo -u $MC_USER bash -c "echo -e '\nenable-rcon=true\nrcon.port=25576\nrcon.password=$RCON_PASS_VANILLA' >> $VANILLA_DIR/server.properties"
        echo "  OK: RCON aktiviert (Port 25576)"
    fi
    # Port sicherstellen
    if grep -q "server-port=25565" "$VANILLA_DIR/server.properties"; then
        echo "  OK: Port 25565"
    fi
else
    # Neue server.properties erstellen
    sudo -u $MC_USER tee "$VANILLA_DIR/server.properties" > /dev/null << 'PROPS'
# Minecraft Server Properties — Vanilla/Paper
server-port=25565
enable-rcon=true
rcon.port=25576
rcon.password=RCON_PASS_PLACEHOLDER
max-players=20
motd=\u00a76Ostfront \u00a7f| \u00a7aVanilla/Paper
difficulty=normal
gamemode=survival
view-distance=12
simulation-distance=10
white-list=false
enforce-whitelist=false
online-mode=true
spawn-protection=0
enable-command-block=false
max-tick-time=60000
PROPS
    sudo -u $MC_USER sed -i "s/RCON_PASS_PLACEHOLDER/$RCON_PASS_VANILLA/" "$VANILLA_DIR/server.properties"
    echo "  OK: server.properties erstellt (RCON Port 25576)"
fi

# =============================================================================
# 4. Better MC Verzeichnis vorbereiten
# =============================================================================

echo ""
echo "[4/8] Better MC Verzeichnis vorbereiten..."

# Better MC muss manuell hochgeladen werden (Modpack)
# Hier nur EULA + RCON-Konfiguration vorbereiten

sudo -u $MC_USER bash -c "echo 'eula=true' > $BMC_DIR/eula.txt"

if [ ! -f "$BMC_DIR/server.properties" ]; then
    sudo -u $MC_USER tee "$BMC_DIR/server.properties" > /dev/null << 'PROPS'
# Minecraft Server Properties — Better MC
server-port=25566
enable-rcon=true
rcon.port=25575
rcon.password=RCON_PASS_PLACEHOLDER
max-players=20
motd=\u00a76Ostfront \u00a7f| \u00a7bBetter MC
difficulty=normal
gamemode=survival
view-distance=10
simulation-distance=8
white-list=false
enforce-whitelist=false
online-mode=true
spawn-protection=0
enable-command-block=false
max-tick-time=90000
PROPS
    sudo -u $MC_USER sed -i "s/RCON_PASS_PLACEHOLDER/$RCON_PASS_BMC/" "$BMC_DIR/server.properties"
    echo "  OK: server.properties (RCON Port 25575)"
else
    echo "  SKIP: server.properties existiert bereits"
    echo "  WICHTIG: Stelle sicher dass RCON aktiviert ist:"
    echo "    enable-rcon=true"
    echo "    rcon.port=25575"
    echo "    rcon.password=$RCON_PASS_BMC"
fi

echo ""
echo "  HINWEIS: Better MC Modpack-Dateien muessen manuell nach"
echo "  $BMC_DIR hochgeladen werden (server.jar + mods/ etc.)"

# =============================================================================
# 5. systemd Services installieren
# =============================================================================

echo ""
echo "[5/8] systemd Services installieren..."

# Alten minecraft.service deaktivieren
if [ -f /etc/systemd/system/minecraft.service ]; then
    systemctl disable minecraft.service 2>/dev/null || true
    echo "  INFO: Alter minecraft.service deaktiviert"
fi

# Vanilla/Paper Service (mit Aikar-Flags)
cat > /etc/systemd/system/minecraft-vanilla.service << EOF
[Unit]
Description=Minecraft Vanilla/Paper Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=minecraft
Group=minecraft
WorkingDirectory=$VANILLA_DIR

ExecStart=/usr/bin/java -Xms2G -Xmx4G $AIKAR_FLAGS -jar server.jar nogui
ExecStop=/usr/bin/rcon-cli --host 127.0.0.1 --port 25576 --password $RCON_PASS_VANILLA stop
TimeoutStopSec=60

Restart=on-failure
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=3

LimitNOFILE=65535
MemoryMax=6G
CPUQuota=200%

StandardOutput=journal
StandardError=journal
SyslogIdentifier=minecraft-vanilla

[Install]
WantedBy=multi-user.target
EOF

# Better MC Service (mit Aikar-Flags, mehr RAM)
cat > /etc/systemd/system/minecraft-bmc.service << EOF
[Unit]
Description=Minecraft Better MC Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=minecraft
Group=minecraft
WorkingDirectory=$BMC_DIR

ExecStart=/usr/bin/java -Xms4G -Xmx8G $AIKAR_FLAGS -jar server.jar nogui
ExecStop=/usr/bin/rcon-cli --host 127.0.0.1 --port 25575 --password $RCON_PASS_BMC stop
TimeoutStopSec=60

Restart=on-failure
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=3

LimitNOFILE=65535
MemoryMax=10G
CPUQuota=200%

StandardOutput=journal
StandardError=journal
SyslogIdentifier=minecraft-bmc

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "  OK: minecraft-vanilla.service"
echo "  OK: minecraft-bmc.service"

# =============================================================================
# 6. rcon-cli installieren (fuer ExecStop)
# =============================================================================

echo ""
echo "[6/8] rcon-cli installieren..."

if ! command -v rcon-cli &> /dev/null; then
    RCON_CLI_VERSION="0.10.3"
    RCON_CLI_URL="https://github.com/gorcon/rcon-cli/releases/download/v${RCON_CLI_VERSION}/rcon-${RCON_CLI_VERSION}-amd64_linux.tar.gz"

    cd /tmp
    rm -f rcon-cli.tar.gz
    wget -O rcon-cli.tar.gz "$RCON_CLI_URL"
    tar xzf rcon-cli.tar.gz
    if [ -f "rcon-${RCON_CLI_VERSION}-amd64_linux/rcon" ]; then
        mv "rcon-${RCON_CLI_VERSION}-amd64_linux/rcon" /usr/local/bin/rcon-cli
    elif [ -f "rcon" ]; then
        mv rcon /usr/local/bin/rcon-cli
    fi
    chmod +x /usr/local/bin/rcon-cli
    ln -sf /usr/local/bin/rcon-cli /usr/bin/rcon-cli
    rm -rf rcon-cli.tar.gz rcon-${RCON_CLI_VERSION}-amd64_linux rcon
    echo "  OK: rcon-cli v${RCON_CLI_VERSION} installiert"
else
    echo "  SKIP: rcon-cli bereits installiert ($(rcon-cli --version 2>&1 | head -1))"
fi

# =============================================================================
# 7. Sudoers fuer botuser erweitern (MC Services)
# =============================================================================

echo ""
echo "[7/8] Sudoers fuer botuser erweitern..."

SUDOERS_FILE="/etc/sudoers.d/botuser"
MC_SUDOERS_NEEDED=false

# Pruefen ob MC-Regeln schon vorhanden
if ! grep -q "minecraft-vanilla" "$SUDOERS_FILE" 2>/dev/null; then
    MC_SUDOERS_NEEDED=true
fi

if [ "$MC_SUDOERS_NEEDED" = true ]; then
    cat >> "$SUDOERS_FILE" << 'SUDOERS'

# Minecraft Server Management
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl start minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl status minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl show minecraft-vanilla.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl start minecraft-bmc.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop minecraft-bmc.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart minecraft-bmc.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl status minecraft-bmc.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active minecraft-bmc.service
botuser ALL=(ALL) NOPASSWD: /usr/bin/systemctl show minecraft-bmc.service
SUDOERS

    # Validieren
    if visudo -c -f "$SUDOERS_FILE" 2>/dev/null; then
        echo "  OK: Sudoers erweitert"
    else
        echo "  FEHLER: Sudoers-Syntax fehlerhaft!"
        exit 1
    fi
else
    echo "  SKIP: MC-Regeln bereits vorhanden"
fi

# =============================================================================
# 8. Firewall (UFW)
# =============================================================================

echo ""
echo "[8/8] Firewall-Regeln..."

# Vanilla: 25565
if ! ufw status | grep -q "25565"; then
    ufw allow 25565/tcp comment "Minecraft Vanilla"
    echo "  OK: Port 25565/tcp geoeffnet"
else
    echo "  SKIP: Port 25565 bereits offen"
fi

# Better MC: 25566
if ! ufw status | grep -q "25566"; then
    ufw allow 25566/tcp comment "Minecraft Better MC"
    echo "  OK: Port 25566/tcp geoeffnet"
else
    echo "  SKIP: Port 25566 bereits offen"
fi

# RCON nur lokal (kein UFW-Eintrag noetig — standardmaessig geblockt)
echo "  INFO: RCON-Ports (25575, 25576) nur lokal erreichbar (kein UFW allow)"

# =============================================================================
# Zusammenfassung
# =============================================================================

echo ""
echo "========================================="
echo " Setup abgeschlossen!"
echo "========================================="
echo ""
echo "Vanilla/Paper Server:"
echo "  Verzeichnis: $VANILLA_DIR"
echo "  Port:        25565"
echo "  RCON:        127.0.0.1:25576"
echo "  RCON-Pass:   $RCON_PASS_VANILLA"
echo "  Service:     minecraft-vanilla.service"
echo ""
echo "Better MC Server:"
echo "  Verzeichnis: $BMC_DIR"
echo "  Port:        25566"
echo "  RCON:        127.0.0.1:25575"
echo "  RCON-Pass:   $RCON_PASS_BMC"
echo "  Service:     minecraft-bmc.service"
echo ""
echo "Naechste Schritte:"
echo "  1. RCON-Passwoerter AENDERN (in den .service-Dateien + server.properties)"
echo "  2. Better MC Modpack nach $BMC_DIR hochladen"
echo "  3. Vanilla starten:   sudo systemctl start minecraft-vanilla"
echo "  4. Better MC starten: sudo systemctl start minecraft-bmc"
echo "  5. Bot ENV-Vars in /home/botuser/Discord_Bots/config/.env setzen"
echo ""
echo "ENV-Variablen fuer den Bot:"
echo "  MC_VANILLA_SERVICE=minecraft-vanilla.service"
echo "  MC_VANILLA_DISPLAY_NAME=Vanilla/Paper"
echo "  MC_VANILLA_PATH=$VANILLA_DIR"
echo "  MC_VANILLA_WORLD_PATH=$VANILLA_DIR/world"
echo "  MC_VANILLA_RCON_HOST=127.0.0.1"
echo "  MC_VANILLA_RCON_PORT=25576"
echo "  MC_VANILLA_RCON_PASSWORD=$RCON_PASS_VANILLA"
echo "  MC_VANILLA_BACKUP_PATH=$BACKUP_DIR_VANILLA"
echo "  MC_VANILLA_LOG_PATH=$VANILLA_DIR/logs/latest.log"
echo "  MC_VANILLA_GAME_CHAT_CHANNEL_ID=0"
echo ""
echo "  MC_BMC_SERVICE=minecraft-bmc.service"
echo "  MC_BMC_DISPLAY_NAME=Better MC"
echo "  MC_BMC_PATH=$BMC_DIR"
echo "  MC_BMC_WORLD_PATH=$BMC_DIR/world"
echo "  MC_BMC_RCON_HOST=127.0.0.1"
echo "  MC_BMC_RCON_PORT=25575"
echo "  MC_BMC_RCON_PASSWORD=$RCON_PASS_BMC"
echo "  MC_BMC_BACKUP_PATH=$BACKUP_DIR_BMC"
echo "  MC_BMC_LOG_PATH=$BMC_DIR/logs/latest.log"
echo "  MC_BMC_GAME_CHAT_CHANNEL_ID=0"
