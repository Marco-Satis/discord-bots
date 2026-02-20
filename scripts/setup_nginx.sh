#!/bin/bash
# =============================================================================
# Nginx Setup fuer Server Status-Seite
# Muss als root ausgefuehrt werden: sudo bash scripts/setup_nginx.sh
# =============================================================================

set -euo pipefail

# Konfiguration
STATUS_PATH="/var/www/status"
BOT_USER="botuser"
NGINX_PORT=8080
NGINX_CONF="/etc/nginx/sites-available/server-status"
NGINX_LINK="/etc/nginx/sites-enabled/server-status"

echo "=== Server Status Nginx Setup ==="
echo ""

# Root-Check
if [ "$(id -u)" -ne 0 ]; then
    echo "FEHLER: Dieses Script muss als root ausgefuehrt werden!"
    echo "Verwende: sudo bash $0"
    exit 1
fi

# 1. Nginx installieren
echo "[1/5] Nginx installieren..."
if ! command -v nginx &> /dev/null; then
    apt update -qq
    apt install -y nginx
    echo "  -> Nginx installiert"
else
    echo "  -> Nginx bereits vorhanden"
fi

# 2. Status-Verzeichnis erstellen
echo "[2/5] Status-Verzeichnis erstellen..."
mkdir -p "$STATUS_PATH"
chown "$BOT_USER":"$BOT_USER" "$STATUS_PATH"
chmod 755 "$STATUS_PATH"
echo "  -> $STATUS_PATH erstellt (Owner: $BOT_USER)"

# 3. Nginx-Config deployen
echo "[3/5] Nginx-Config erstellen..."
cat > "$NGINX_CONF" << NGINX_EOF
server {
    listen ${NGINX_PORT};
    listen [::]:${NGINX_PORT};
    server_name _;

    root ${STATUS_PATH};
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;

        # Cache-Header fuer haeufiges Refresh
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma "no-cache";
        add_header Expires "0";
    }

    # Sicherheit
    location ~ /\. {
        deny all;
    }

    access_log /var/log/nginx/status-access.log;
    error_log /var/log/nginx/status-error.log;
}
NGINX_EOF

# Symlink erstellen (falls noch nicht vorhanden)
if [ ! -L "$NGINX_LINK" ]; then
    ln -s "$NGINX_CONF" "$NGINX_LINK"
fi

echo "  -> Config erstellt: $NGINX_CONF (Port $NGINX_PORT)"

# 4. UFW Port oeffnen (falls UFW aktiv)
echo "[4/5] Firewall konfigurieren..."
if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
    ufw allow "$NGINX_PORT/tcp" comment "Server Status Page"
    echo "  -> UFW: Port $NGINX_PORT/tcp erlaubt"
else
    echo "  -> UFW nicht aktiv, uebersprungen"
fi

# 5. Nginx starten und enablen
echo "[5/5] Nginx starten..."
nginx -t
systemctl enable nginx
systemctl restart nginx
echo "  -> Nginx gestartet und enabled"

echo ""
echo "=== Setup abgeschlossen ==="
echo "Status-Seite erreichbar unter: http://<server-ip>:${NGINX_PORT}/"
echo ""
echo "Vergiss nicht, WEB_STATUS_ENABLED=true in config/.env zu setzen!"
