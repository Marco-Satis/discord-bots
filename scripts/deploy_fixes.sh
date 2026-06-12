#!/bin/bash
# ============================================================
# Discord Bot System V3 — Deployment der CHANGELOG_FIXES
# Datum: 2026-02-18
# Dieses Script auf dem Server als botuser ausführen
# ============================================================
set -e

echo "=== Discord Bot V3 — Fix Deployment ==="
echo ""

# --- Punkt 3: drop-caches.sh installieren ---
echo "[3/8] Installiere drop-caches.sh..."
if [ -f "/home/botuser/Discord_Bots/scripts/drop-caches.sh" ]; then
    sudo cp /home/botuser/Discord_Bots/scripts/drop-caches.sh /usr/local/bin/drop-caches.sh
    sudo chmod 755 /usr/local/bin/drop-caches.sh
    sudo chown root:root /usr/local/bin/drop-caches.sh
    echo "  ✅ drop-caches.sh installiert"
else
    echo "  ❌ drop-caches.sh nicht gefunden in scripts/"
fi

echo ""

# --- Punkt 4: sudoers aktualisieren ---
echo "[4/8] Aktualisiere sudoers..."
if [ -f "/home/botuser/Discord_Bots/systemd/botuser-sudoers" ]; then
    # Validate syntax first
    sudo visudo -c -f /home/botuser/Discord_Bots/systemd/botuser-sudoers
    if [ $? -eq 0 ]; then
        sudo cp /home/botuser/Discord_Bots/systemd/botuser-sudoers /etc/sudoers.d/botuser
        sudo chmod 440 /etc/sudoers.d/botuser
        echo "  ✅ sudoers aktualisiert und validiert"
    else
        echo "  ❌ sudoers Syntax-Fehler! Nicht kopiert."
    fi
else
    echo "  ❌ botuser-sudoers nicht gefunden in systemd/"
fi

echo ""

# --- Punkt 5: v3_optimizer_update Ordner (Info) ---
echo "[5/8] v3_optimizer_update/ — Alle Dateien wurden integriert."
echo "  ℹ️  Ordner kann bestehen bleiben oder gelöscht werden:"
echo "     rm -rf /home/botuser/Discord_Bots/v3_optimizer_update/"

echo ""

# --- Punkte 1, 2, 6: Manuelle Schritte ---
echo "============================================"
echo "  MANUELLE SCHRITTE (auf dem Server):      "
echo "============================================"
echo ""
echo "[1/8] API Token generieren:"
echo "  1. Satisfactory Server starten"
echo "  2. Im Spiel mit Admin verbinden"
echo "  3. API Token generieren (Admin Panel)"
echo "  4. In config/.env eintragen:"
echo "     API_TOKEN=<generierter_token>"
echo ""
echo "[2/8] Discord Tokens prüfen:"
echo "  - DISCORD_TOKEN_MANAGER in config/.env"
echo "  - DISCORD_TOKEN_WATCHDOG in config/.env"
echo "  (Nur auf dem Server, NICHT in Git/OneDrive!)"
echo ""
echo "[6/8] SMTP Passwort setzen:"
echo "  - SMTP_PASS in config/.env"
echo "  - Outlook App-Passwort verwenden"
echo ""
echo "[7/8] Type Hints — automatisch korrigiert ✅"
echo "[8/8] Bare Exceptions — automatisch korrigiert ✅"
echo ""

# --- Bots neustarten ---
echo "Bots neustarten? (j/n)"
read -r answer
if [ "$answer" = "j" ]; then
    echo "Starte Bots neu..."
    sudo systemctl restart operator-bot.service
    sudo systemctl restart recon-bot.service
    sleep 3
    echo "Status:"
    sudo systemctl is-active operator-bot.service
    sudo systemctl is-active recon-bot.service
    echo "✅ Bots neugestartet"
else
    echo "Bots NICHT neugestartet. Manuell mit:"
    echo "  sudo systemctl restart operator-bot.service"
    echo "  sudo systemctl restart recon-bot.service"
fi

echo ""
echo "=== Deployment abgeschlossen ==="
