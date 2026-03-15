#!/bin/bash
# =============================================================================
# Fix-Script: Schritte 6-8 nachholen (rcon-cli, Sudoers, Firewall)
# Ausfuehren als: sudo bash setup_minecraft_fix.sh
# =============================================================================

set -e

RCON_PASS_VANILLA="ChangeMe_Vanilla_2026"
RCON_PASS_BMC="ChangeMe_BMC_2026"

# =============================================================================
# 6. rcon-cli installieren
# =============================================================================

echo ""
echo "[6/8] rcon-cli installieren..."

# Alte fehlgeschlagene Datei aufraeumen
rm -f /tmp/rcon-cli.tar.gz

RCON_CLI_VERSION="0.10.3"
RCON_CLI_URL="https://github.com/gorcon/rcon-cli/releases/download/v${RCON_CLI_VERSION}/rcon-${RCON_CLI_VERSION}-amd64_linux.tar.gz"

cd /tmp
echo "  Download von $RCON_CLI_URL ..."
wget -O rcon-cli.tar.gz "$RCON_CLI_URL"

# Pruefen ob Download erfolgreich
FILE_SIZE=$(stat -c%s rcon-cli.tar.gz 2>/dev/null || echo "0")
if [ "$FILE_SIZE" -lt 1000 ]; then
    echo "  FEHLER: Download fehlgeschlagen (Datei nur $FILE_SIZE Bytes)"
    exit 1
fi

tar xzf rcon-cli.tar.gz
# Pruefen welche Verzeichnisstruktur extrahiert wurde
if [ -f "rcon-${RCON_CLI_VERSION}-amd64_linux/rcon" ]; then
    mv "rcon-${RCON_CLI_VERSION}-amd64_linux/rcon" /usr/local/bin/rcon-cli
elif [ -f "rcon" ]; then
    mv rcon /usr/local/bin/rcon-cli
else
    echo "  Extrahierte Dateien:"
    ls -la /tmp/rcon*
    echo "  FEHLER: rcon Binary nicht gefunden!"
    exit 1
fi

chmod +x /usr/local/bin/rcon-cli
ln -sf /usr/local/bin/rcon-cli /usr/bin/rcon-cli
rm -rf /tmp/rcon-cli.tar.gz /tmp/rcon-${RCON_CLI_VERSION}-amd64_linux /tmp/rcon
echo "  OK: rcon-cli v${RCON_CLI_VERSION} installiert"
rcon-cli --version 2>&1 || true

# =============================================================================
# 7. Sudoers fuer botuser erweitern (MC Services)
# =============================================================================

echo ""
echo "[7/8] Sudoers fuer botuser erweitern..."

SUDOERS_FILE="/etc/sudoers.d/botuser"
MC_SUDOERS_NEEDED=false

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

if ! ufw status | grep -q "25565"; then
    ufw allow 25565/tcp comment "Minecraft Vanilla"
    echo "  OK: Port 25565/tcp geoeffnet"
else
    echo "  SKIP: Port 25565 bereits offen"
fi

if ! ufw status | grep -q "25566"; then
    ufw allow 25566/tcp comment "Minecraft Better MC"
    echo "  OK: Port 25566/tcp geoeffnet"
else
    echo "  SKIP: Port 25566 bereits offen"
fi

echo "  INFO: RCON-Ports (25575, 25576) nur lokal erreichbar"

# =============================================================================
# Fertig
# =============================================================================

echo ""
echo "========================================="
echo " Fix-Script abgeschlossen!"
echo "========================================="
echo ""
echo "Teste rcon-cli:"
echo "  rcon-cli --version"
echo ""
echo "Teste Sudoers (als botuser):"
echo "  sudo -u botuser sudo -l | grep minecraft"
echo ""
echo "Vanilla starten:"
echo "  sudo systemctl start minecraft-vanilla"
echo "  sudo journalctl -fu minecraft-vanilla"
