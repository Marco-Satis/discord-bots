#!/bin/bash
# =============================================================================
# Setup HTTPS Reverse-Proxy fuer das Web-Dashboard
# Verwendet Let's Encrypt (Certbot) fuer ein echtes SSL-Zertifikat
# mit der Domain aus WEB_DOMAIN bzw. dem ersten Argument
#
# Aufruf: sudo bash scripts/setup_https_proxy.sh
# =============================================================================

set -euo pipefail

# Domain: 1. Argument, sonst WEB_DOMAIN aus der Umgebung, sonst Platzhalter.
DOMAIN="${1:-${WEB_DOMAIN:-beispiel.duckdns.org}}"
NGINX_CONF="/etc/nginx/sites-available/dashboard-proxy"
NGINX_LINK="/etc/nginx/sites-enabled/dashboard-proxy"
HTTPS_PORT=443
BACKEND_PORT=8080
EMAIL="marco@users.noreply.github.com"

echo "=== HTTPS Reverse-Proxy Setup mit Let's Encrypt ==="
echo "Domain: ${DOMAIN}"
echo ""

# 1. Certbot installieren falls nicht vorhanden
echo "[1/5] Certbot pruefen/installieren..."
if ! command -v certbot &>/dev/null; then
    apt-get update -qq
    apt-get install -y certbot python3-certbot-nginx
    echo "    Certbot installiert."
else
    echo "    Certbot bereits vorhanden."
fi

# 2. Nginx-Konfiguration erstellen (erstmal HTTP fuer Certbot-Challenge)
echo "[2/5] Nginx-Konfiguration erstellen..."
cat > "${NGINX_CONF}" <<'NGINX_EOF'
# HTTPS Reverse-Proxy fuer Discord Bot Web-Dashboard
# Automatisch erstellt von setup_https_proxy.sh
# Certbot wird die SSL-Konfiguration automatisch hinzufuegen.

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Logging
    access_log /var/log/nginx/dashboard_access.log;
    error_log  /var/log/nginx/dashboard_error.log;

    # Security Headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Proxy zum Dashboard-Backend (Uvicorn)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket-Support (fuer HTMX + Dashboard WS)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Statische Dateien direkt ausliefern (schneller)
    location /static/ {
        alias /home/botuser/Discord_Bots/web/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
NGINX_EOF

echo "    Konfiguration: ${NGINX_CONF}"

# 3. Symlink erstellen falls nicht vorhanden
echo "[3/5] Nginx aktivieren..."
if [ ! -L "${NGINX_LINK}" ]; then
    ln -sf "${NGINX_CONF}" "${NGINX_LINK}"
fi

# Nginx-Konfiguration testen und neu laden
nginx -t
systemctl reload nginx

# 4. Firewall-Ports oeffnen
echo "[4/5] Firewall-Ports pruefen..."
ufw allow 80/tcp comment 'HTTP fuer Certbot' 2>/dev/null || true
ufw allow 443/tcp comment 'HTTPS Dashboard' 2>/dev/null || true

# 5. Let's Encrypt Zertifikat anfordern
echo "[5/5] Let's Encrypt Zertifikat anfordern..."
certbot --nginx \
    -d "${DOMAIN}" \
    --email "${EMAIL}" \
    --agree-tos \
    --no-eff-email \
    --redirect

echo ""
echo "=== Setup abgeschlossen ==="
echo ""
echo "Dashboard erreichbar unter: https://${DOMAIN}"
echo ""
echo "Zertifikat wird automatisch erneuert (certbot timer)."
echo "Naechste Erneuerung pruefen: sudo certbot renew --dry-run"
echo ""
