# Deployment-Checkliste — Discord Bot System v3.2.0

> Zuletzt aktualisiert: 2026-02-21
> Zielplattform: Ubuntu 22.04 LTS (Dedicated Server / VPS)

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [Installation](#2-installation)
3. [ENV-Variablen ausfuellen](#3-env-variablen-ausfuellen)
4. [Systemd Services einrichten](#4-systemd-services-einrichten)
5. [Nginx Reverse Proxy](#5-nginx-reverse-proxy)
6. [Post-Deployment Verifikation](#6-post-deployment-verifikation)
7. [Rollback-Anleitung](#7-rollback-anleitung)

---

## 1. Voraussetzungen

### Betriebssystem & Laufzeitumgebung

- [ ] Ubuntu 22.04 LTS (oder kompatibles Debian-basiertes System)
- [ ] Python 3.10 oder hoeher (`python3 --version`)
- [ ] discord.py 2.3 oder hoeher (wird ueber requirements.txt installiert)

### Benoetigte Systempakete

```bash
sudo apt update && sudo apt install -y \
  python3-pip \
  python3-venv \
  git \
  nginx \
  certbot \
  python3-certbot-nginx \
  steamcmd
```

### HTTPS-Domain

- [ ] DuckDNS-Domain eingerichtet (z.B. `dein-server.duckdns.org`)
- [ ] DNS zeigt auf die Server-IP (`dig +short dein-server.duckdns.org`)
- [ ] Certbot SSL-Zertifikat vorhanden oder bereit zur Erstellung:

```bash
sudo certbot --nginx -d dein-server.duckdns.org
```

### Firewall-Ports

```bash
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # HTTP (Certbot / Redirect)
sudo ufw allow 443/tcp    # HTTPS (Dashboard)
sudo ufw allow 7777/udp   # Satisfactory Game
sudo ufw allow 15000/udp  # Satisfactory Query
sudo ufw enable
```

---

## 2. Installation

### 2.1 Benutzer anlegen (empfohlen)

```bash
sudo useradd -m -s /bin/bash botuser
sudo su - botuser
```

### 2.2 Repository klonen

```bash
cd /home/botuser
git clone https://github.com/DEIN_USER/Discord_Bots.git
cd Discord_Bots
git checkout v3.2.0    # Auf Release-Tag wechseln
```

### 2.3 Python Virtual Environment erstellen

```bash
python3 -m venv venv && source venv/bin/activate
```

### 2.4 Abhaengigkeiten installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.5 Konfiguration anlegen

```bash
cp config/.env.example config/.env && nano config/.env
```

> **Wichtig:** Niemals die `.env`-Datei ins Git-Repository committen!

---

## 3. ENV-Variablen ausfuellen

Oeffne `config/.env` und fuelle **alle Pflichtfelder** aus.
Variablen mit Standardwerten koennen bei Bedarf angepasst werden.

### 3.1 Discord — Tokens & Server

| Variable | Beschreibung | Pflicht |
|---|---|---|
| `DISCORD_TOKEN_MANAGER` | Bot-Token fuer den GameServer Bot (Bot 1). Aus dem [Discord Developer Portal](https://discord.com/developers/applications) kopieren. | Ja |
| `DISCORD_TOKEN_WATCHDOG` | Bot-Token fuer den Monitor Bot (Bot 2). Separater Bot-Account empfohlen. | Ja |
| `ADMIN_BOT_TOKEN` | Bot-Token fuer den Admin Bot (Bot 3). Nur noetig, wenn Admin-Bot genutzt wird. | Nein |
| `GUILD_ID` | Die Server-ID des Discord-Servers. Rechtsklick auf Server > "Server-ID kopieren" (Entwicklermodus muss aktiv sein). | Ja |

### 3.2 Discord — Rollen & Berechtigungen

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `OWNER_ID` | Discord User-ID des Bot-Eigentuemers (volle Kontrolle). | `1000000000000000001` |
| `ADMIN_ROLE_ID` | Rollen-ID der Admin-Rolle (kann Server steuern). | `1000000000000000002` |
| `SATISFACTORY_ROLE_ID` | Rollen-ID fuer Satisfactory-Spieler. | `1000000000000000004` |
| `MINECRAFT_ROLE_ID` | Rollen-ID fuer Minecraft-Spieler (`0` = deaktiviert). | `0` |
| `NOTIFY_ROLE_ID` | Rollen-ID fuer Benachrichtigungen (optional). | leer |

### 3.3 Discord — Channels

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `ADMIN_LOG_CHANNEL_ID` | Channel fuer Admin-Logs und Bot-Benachrichtigungen. | `1000000000000000005` |
| `PUBLIC_STATUS_CHANNEL_ID` | Oeffentlicher Status-Channel fuer Spieler. | `1000000000000000003` |
| `STATUS_EMBED_CHANNEL_ID` | Channel fuer Dashboard-Embed (optional). | leer |
| `VOICE_STATS_CATEGORY_ID` | Kategorie fuer Voice-Channel-Statistiken (optional). | leer |

### 3.4 Satisfactory — Server

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `SATISFACTORY_SERVICE` | Name des systemd-Service fuer den Satisfactory Server. | `satisfactory.service` |
| `SATISFACTORY_USER` | Linux-Benutzer, unter dem der Server laeuft. | `satisfactory` |
| `SATISFACTORY_SERVER_PATH` | Pfad zum Dedicated Server. | `/home/satisfactory/SatisfactoryDedicatedServer` |
| `SATISFACTORY_SAVE_PATH` | Pfad zu den Savegames. | `/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames` |
| `API_HOST` | Satisfactory API Host. | `127.0.0.1` |
| `API_PORT` | Satisfactory API Port. | `7777` |
| `API_TOKEN` | **Satisfactory API Token.** Aus dem Server-Admin-Panel kopieren. | **Pflicht** |
| `API_VERIFY_SSL` | SSL-Verifikation fuer die API. | `false` |
| `STEAMCMD_PATH` | Pfad zu SteamCMD (fuer Server-Updates). | `/usr/games/steamcmd` |

### 3.5 Minecraft — Server (optional, pro Server ein Block)

Prefix-Schema: `MC_{SERVER_ID}_*` — Server ist aktiv, sobald `MC_{ID}_SERVICE` gesetzt ist.

| Variable (Beispiel BMC) | Beschreibung |
|---|---|
| `MC_BMC_DISPLAY_NAME` | Anzeigename im Discord (`Better MC`). |
| `MC_BMC_SERVICE` | systemd-Service-Name (`minecraft-bmc.service`). |
| `MC_BMC_PATH` | Server-Verzeichnis. |
| `MC_BMC_WORLD_PATH` | Pfad zum World-Ordner. |
| `MC_BMC_RCON_HOST` | RCON-Host (`127.0.0.1`). |
| `MC_BMC_RCON_PORT` | RCON-Port (`25575`). |
| `MC_BMC_RCON_PASSWORD` | **RCON-Passwort** (Pflicht fuer Server-Steuerung). |
| `MC_BMC_BACKUP_PATH` | Backup-Verzeichnis. |
| `MC_BMC_LOG_PATH` | Pfad zur `latest.log`. |
| `MC_BMC_GAME_CHAT_CHANNEL_ID` | Discord Chat-Bridge Channel (`0` = deaktiviert). |

> Fuer Vanilla/Paper-Server analog mit Prefix `MC_VANILLA_*`.

### 3.6 Backup & Cloud

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `BACKUP_PATH` | Lokaler Backup-Pfad. | `/home/botuser/Discord_Bots/backups` |
| `ONEDRIVE_ENABLED` | OneDrive Cloud-Backup aktivieren (benoetigt `rclone`). | `false` |
| `ONEDRIVE_REMOTE` | rclone Remote-Name. | `onedrive` |
| `ONEDRIVE_PATH` | Zielordner in OneDrive. | `SatisfactoryBackups` |

### 3.7 E-Mail-Benachrichtigungen (optional)

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `EMAIL_ENABLED` | E-Mail-Benachrichtigungen aktivieren. | `false` |
| `SMTP_HOST` | SMTP-Server. | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP-Port. | `587` |
| `SMTP_USER` | SMTP-Benutzername (E-Mail-Adresse). | leer |
| `SMTP_PASS` | SMTP-Passwort / App-Passwort. | leer |
| `EMAIL_FROM` | Absender-Adresse. | leer |
| `EMAIL_TO` | Empfaenger-Adresse. | leer |

### 3.8 TeamSpeak — ServerQuery (optional)

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `TS_ENABLED` | TeamSpeak-Integration aktivieren. | `false` |
| `TS_HOST` | TeamSpeak Server IP/Hostname. | leer |
| `TS_PORT` | ServerQuery Port. | `10011` |
| `TS_USER` | ServerQuery Benutzername. | `serveradmin` |
| `TS_PASSWORD` | ServerQuery Passwort. | leer |
| `TS_SERVER_ID` | Virtuelle Server-ID. | `1` |

### 3.9 Web-Dashboard

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `WEB_ENABLED` | Dashboard aktivieren. | `false` |
| `WEB_PORT` | Port fuer das Dashboard. | `8080` |
| `WEB_HTTPS` | Secure Cookies setzen (bei HTTPS-Domain auf `true`). | `true` |
| `WEB_DOMAIN` | Domain fuer CORS-Policy. | `marco-satisfactory.duckdns.org` |
| `WEB_SECRET_KEY` | **JWT Secret (mindestens 32 Zeichen).** Generieren mit: | **Pflicht** |
| `WEB_ADMIN_USER` | Fallback-Login Benutzername. | `admin` |
| `WEB_ADMIN_PASS_HASH` | **bcrypt-Hash des Admin-Passworts.** Generieren mit: | **Pflicht** |
| `DISCORD_CLIENT_ID` | Discord OAuth2 Application Client ID. | leer |
| `DISCORD_CLIENT_SECRET` | Discord OAuth2 Application Client Secret. | leer |
| `DISCORD_REDIRECT_URI` | OAuth2 Redirect URI. | `http://localhost:8080/auth/discord/callback` |
| `WEB_WEBMIN_URL` | Webmin-URL fuer System-Seite (iframe). | `https://localhost:10000` |

#### JWT Secret generieren

```bash
openssl rand -hex 32
```

Ergebnis in `WEB_SECRET_KEY` eintragen.

#### bcrypt-Passwort-Hash generieren

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'DEIN_PASSWORT', bcrypt.gensalt()).decode())"
```

> **Wichtig:** `DEIN_PASSWORT` durch das gewuenschte Passwort ersetzen!
> Den ausgegebenen Hash (beginnt mit `$2b$`) in `WEB_ADMIN_PASS_HASH` eintragen.

### 3.10 Sonstige Variablen

| Variable | Beschreibung | Standardwert |
|---|---|---|
| `SERVER_IP` | Oeffentliche IP des Servers. | `203.0.113.10` |
| `WEB_STATUS_ENABLED` | Statische HTML-Statusseite generieren. | `false` |
| `WEB_STATUS_PATH` | Ausgabe-Verzeichnis fuer Statusseite. | `/var/www/status` |
| `GPG_PASSPHRASE` | Passphrase fuer GPG-verschluesselte Config-Backups (optional). | leer |
| `MC_BMC_MODPACK_ID` | Modrinth/CurseForge Projekt-ID fuer Update-Check. | leer |
| `MC_BMC_MODPACK_VERSION` | Aktuell installierte Modpack-Version. | leer |
| `MC_BMC_MODPACK_SOURCE` | `modrinth` oder `curseforge`. | `modrinth` |
| `CURSEFORGE_API_KEY` | CurseForge API Key (nur bei `source=curseforge`). | leer |

---

## 4. Systemd Services einrichten

### 4.1 GameServer Bot (gameserver-bot.service)

```bash
sudo nano /etc/systemd/system/gameserver-bot.service
```

```ini
[Unit]
Description=Discord GameServer Bot (v3.2.0)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/home/botuser/Discord_Bots
ExecStart=/home/botuser/Discord_Bots/venv/bin/python3 bots/gameserver_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=/home/botuser/Discord_Bots/config/.env

[Install]
WantedBy=multi-user.target
```

### 4.2 Monitor Bot (monitor-bot.service)

```bash
sudo nano /etc/systemd/system/monitor-bot.service
```

```ini
[Unit]
Description=Discord Monitor Bot (v3.2.0)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/home/botuser/Discord_Bots
ExecStart=/home/botuser/Discord_Bots/venv/bin/python3 bots/monitor_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=/home/botuser/Discord_Bots/config/.env

[Install]
WantedBy=multi-user.target
```

### 4.3 Admin Bot (admin-bot.service) — optional

```bash
sudo nano /etc/systemd/system/admin-bot.service
```

```ini
[Unit]
Description=Discord Admin Bot (v3.2.0)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/home/botuser/Discord_Bots
ExecStart=/home/botuser/Discord_Bots/venv/bin/python3 bots/admin_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=/home/botuser/Discord_Bots/config/.env

[Install]
WantedBy=multi-user.target
```

### 4.4 Web Dashboard (web-dashboard.service)

```bash
sudo nano /etc/systemd/system/web-dashboard.service
```

```ini
[Unit]
Description=Discord Bot Web Dashboard (v3.2.0)
After=network-online.target gameserver-bot.service
Wants=network-online.target

[Service]
Type=simple
User=botuser
Group=botuser
WorkingDirectory=/home/botuser/Discord_Bots
ExecStart=/home/botuser/Discord_Bots/venv/bin/python3 web/app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=/home/botuser/Discord_Bots/config/.env

[Install]
WantedBy=multi-user.target
```

### 4.5 Services aktivieren und starten

```bash
sudo systemctl daemon-reload

# Pflicht-Services
sudo systemctl enable --now gameserver-bot.service
sudo systemctl enable --now monitor-bot.service
sudo systemctl enable --now web-dashboard.service

# Optional
sudo systemctl enable --now admin-bot.service
```

### 4.6 Status pruefen

```bash
sudo systemctl status gameserver-bot.service
sudo systemctl status monitor-bot.service
sudo systemctl status web-dashboard.service
sudo systemctl status admin-bot.service
```

### 4.7 Logs anzeigen

```bash
# Live-Logs verfolgen
sudo journalctl -u gameserver-bot.service -f

# Letzte 100 Zeilen
sudo journalctl -u monitor-bot.service -n 100 --no-pager
```

---

## 5. Nginx Reverse Proxy

### 5.1 Nginx-Konfiguration erstellen

```bash
sudo nano /etc/nginx/sites-available/discord-bot-dashboard
```

```nginx
server {
    listen 80;
    server_name dein-server.duckdns.org;

    # Redirect HTTP -> HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name dein-server.duckdns.org;

    # SSL-Zertifikate (von Certbot verwaltet)
    ssl_certificate     /etc/letsencrypt/live/dein-server.duckdns.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dein-server.duckdns.org/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Sicherheits-Header
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Proxy zum Web Dashboard (Port 8080)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # WebSocket-Support fuer /ws Endpunkt
    location /ws {
        proxy_pass http://127.0.0.1:8080/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket Timeouts (laenger als Standard)
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }

    # Statische Dateien (optional, falls direkt von Nginx bedient)
    location /static/ {
        alias /home/botuser/Discord_Bots/web/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

### 5.2 Konfiguration aktivieren

```bash
# Symlink erstellen
sudo ln -s /etc/nginx/sites-available/discord-bot-dashboard /etc/nginx/sites-enabled/

# Konfiguration testen
sudo nginx -t

# Nginx neu laden
sudo systemctl reload nginx
```

### 5.3 SSL-Zertifikat einrichten (falls noch nicht geschehen)

```bash
sudo certbot --nginx -d dein-server.duckdns.org
```

> Certbot passt die Nginx-Konfiguration automatisch an und richtet die automatische Erneuerung ein.

---

## 6. Post-Deployment Verifikation

Gehe jeden Punkt durch und bestaetige, dass alles funktioniert:

### 6.1 Services laufen

```bash
# Alle Bot-Services auf einen Blick
sudo systemctl is-active gameserver-bot.service monitor-bot.service web-dashboard.service
```

- [ ] Ausgabe zeigt `active` fuer alle Services

### 6.2 Bots online im Discord

- [ ] GameServer Bot ist online und zeigt gruenen Status
- [ ] Monitor Bot ist online und zeigt gruenen Status
- [ ] Admin Bot ist online (falls aktiviert)

### 6.3 Slash-Commands funktionieren

```
/sat status
```

- [ ] Antwort erscheint mit aktuellem Serverstatus
- [ ] Keine Fehlermeldungen in den Logs:

```bash
sudo journalctl -u gameserver-bot.service -n 50 --no-pager | grep -i error
```

### 6.4 Web-Dashboard erreichbar

- [ ] Dashboard erreichbar unter `https://dein-server.duckdns.org/`
- [ ] Seite laedt ohne SSL-Warnungen
- [ ] Login-Seite wird angezeigt

### 6.5 OAuth2-Login funktioniert

- [ ] "Mit Discord anmelden" leitet korrekt zu Discord weiter
- [ ] Nach Autorisierung Redirect zurueck zum Dashboard
- [ ] Benutzer ist eingeloggt und sieht das Dashboard

> **Hinweis:** Die `DISCORD_REDIRECT_URI` muss exakt mit der im Discord Developer Portal konfigurierten URI uebereinstimmen!

### 6.6 Fallback-Login funktioniert

- [ ] Login mit `WEB_ADMIN_USER` und dem Klartext-Passwort (nicht dem Hash) funktioniert

### 6.7 WebSocket-Verbindung

- [ ] Im Dashboard werden Live-Updates angezeigt (Browser-Konsole auf WebSocket-Fehler pruefen)

### 6.8 Automatisierte Tests ausfuehren

```bash
cd /home/botuser/Discord_Bots
source venv/bin/activate
python3 tests/test_imports.py
```

- [ ] Alle Import-Tests bestanden (keine `ModuleNotFoundError`)

### 6.9 Backup pruefen

```bash
# Manuell ein Backup ausloesen (im Discord)
/sat backup

# Pruefen, ob Datei erstellt wurde
ls -la /home/botuser/Discord_Bots/backups/
```

- [ ] Backup-Datei wurde erfolgreich erstellt

---

## 7. Rollback-Anleitung

Falls nach dem Deployment Probleme auftreten, kann auf die vorherige Version zurueckgerollt werden.

### 7.1 Schnelles Rollback (unveraenderte Dateien sichern)

```bash
cd /home/botuser/Discord_Bots

# Lokale Aenderungen sichern
git stash

# Auf vorherige Version wechseln
git checkout v3.1.0

# Abhaengigkeiten neu installieren
source venv/bin/activate
pip install -r requirements.txt
```

### 7.2 Alle Services neu starten

```bash
sudo systemctl restart gameserver-bot.service
sudo systemctl restart monitor-bot.service
sudo systemctl restart web-dashboard.service
sudo systemctl restart admin-bot.service    # falls aktiviert
```

Oder als Einzeiler:

```bash
sudo systemctl restart gameserver-bot.service monitor-bot.service web-dashboard.service admin-bot.service
```

### 7.3 Rollback verifizieren

```bash
# Version pruefen
cd /home/botuser/Discord_Bots && git describe --tags

# Services pruefen
sudo systemctl status gameserver-bot.service monitor-bot.service web-dashboard.service
```

- [ ] `git describe` zeigt `v3.1.0`
- [ ] Alle Services laufen fehlerfrei
- [ ] Bots sind online im Discord

### 7.4 Zurueck auf v3.2.0

Wenn die Probleme behoben sind:

```bash
cd /home/botuser/Discord_Bots
git checkout v3.2.0
git stash pop          # Gesicherte Aenderungen wiederherstellen (falls vorhanden)
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart gameserver-bot.service monitor-bot.service web-dashboard.service
```

---

## Kurzreferenz — Haeufige Befehle

| Aktion | Befehl |
|---|---|
| Service starten | `sudo systemctl start gameserver-bot.service` |
| Service stoppen | `sudo systemctl stop gameserver-bot.service` |
| Service neustarten | `sudo systemctl restart gameserver-bot.service` |
| Alle Services neustarten | `sudo systemctl restart gameserver-bot.service monitor-bot.service web-dashboard.service` |
| Logs live verfolgen | `sudo journalctl -u gameserver-bot.service -f` |
| Env-Datei bearbeiten | `nano /home/botuser/Discord_Bots/config/.env` |
| Venv aktivieren | `source /home/botuser/Discord_Bots/venv/bin/activate` |
| Abhaengigkeiten aktualisieren | `pip install -r requirements.txt --upgrade` |
| Nginx testen | `sudo nginx -t && sudo systemctl reload nginx` |
| SSL erneuern | `sudo certbot renew --dry-run` |
