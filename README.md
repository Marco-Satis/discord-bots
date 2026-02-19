# Discord Game Server Bots

**2-Bot-System für Satisfactory Dedicated Server Management**

Server: Netcup RS 4000 G12 (8 vCores, 32GB RAM, 1TB NVMe) | Ubuntu 22.04 LTS

---

## Architektur

```
┌─────────────────────────────┐    ┌─────────────────────────────┐
│    GameServer Bot (Bot 1)   │    │     Monitor Bot (Bot 2)     │
│                             │    │                             │
│  /sat start/stop/restart    │    │  Health Check (2 Min)       │
│  /sat players/kick/ban      │    │  Performance Monitor (5 Min)│
│  /sat backup/save/restore   │    │  Dashboard Embed (10 Min)   │
│  /sat settings/config       │    │  Voice Channel Stats        │
│  /chatbridge + /wordfilter  │    │  Auto-Backup (6h)           │
│  /help /server /ping        │    │  Daily Restart (04:00)      │
│                             │    │  Player Tracking + Stats    │
│  Chat Bridge (SAT↔Discord)  │    │  Update Checker (SteamCMD)  │
│  Command Logging            │    │  Crash Detection + Restart  │
│  Word Filter + Anti-Spam    │    │  Discord + Email Alerts     │
└─────────────────────────────┘    └─────────────────────────────┘
         │                                    │
         └──────────┬─────────────────────────┘
                    │
         ┌──────────▼──────────┐
         │  Satisfactory API   │
         │  systemd Service    │
         │  SteamCMD Updates   │
         └─────────────────────┘
```

## Schnellstart

```bash
# 1. Server-Grundeinrichtung (als root)
bash scripts/setup_server.sh

# 2. Konfiguration
cp config/.env.example config/.env
nano config/.env    # Tokens, IDs, etc. eintragen

# 3. Deployment
bash scripts/deploy.sh

# 4. Status
bash scripts/manage_bots.sh status
```

## Verzeichnisstruktur

```
Discord_Bots/
├── bots/                         # Bot-Hauptdateien
│   ├── gameserver_bot.py         #   Bot 1: Spielverwaltung
│   └── monitor_bot.py            #   Bot 2: Monitoring & Scheduler
├── cogs/                         # Discord Slash-Command Gruppen
│   ├── satisfactory_cog.py       #   /sat start/stop/restart/status/cancel
│   ├── satisfactory_players_cog  #   /sat_players/kick/ban + whitelist/blacklist
│   ├── satisfactory_backup_cog   #   /sat_backup/save/download/restore
│   ├── satisfactory_config_cog   #   /sat_settings/playerlimit/console/update
│   ├── satisfactory_blueprints   #   /sat_blueprints upload/list/download/delete
│   ├── chat_bridge_cog.py        #   /chatbridge + /wordfilter
│   ├── general_cog.py            #   /help /server /ping /reload
│   ├── timeout_cog.py            #   /timeout
│   ├── monitor_cog.py            #   /performance /dashboard /stats /report
│   └── scheduler_cog.py          #   /scheduler /update_check + Background Tasks
├── modules/                      # Business Logic
│   ├── satisfactory/             #   Server, API, Whitelist, Blacklist, Blueprints
│   ├── monitoring/               #   Health Check, Performance, Player Tracker, Updates
│   ├── notifications/            #   Discord Notifier, Email Notifier
│   ├── backup/                   #   Backup Manager, OneDrive Backup
│   ├── restart_timer.py          #   Countdown-System mit In-Game Warnungen
│   ├── chat_bridge.py            #   SAT ↔ Discord Chat Relay
│   ├── word_filter.py            #   Wortfilter (partial/exact/regex)
│   ├── anti_spam.py              #   Rate Limiting + Duplikat-Erkennung
│   └── command_logger.py         #   Command Audit Log
├── utils/                        # Hilfsfunktionen
│   ├── config.py                 #   .env + config.json Laden
│   ├── logger.py                 #   Logging Setup
│   ├── formatting.py             #   Embed-Formatierung, Fortschrittsbalken
│   └── permissions.py            #   Berechtigungsprüfung
├── config/
│   ├── .env                      #   Secrets (nicht in Git!)
│   ├── .env.example              #   Vorlage
│   └── config.json               #   Feature-Toggles, Intervalle, Schwellwerte
├── data/                         #   Runtime-Daten (JSON)
├── logs/                         #   Log-Dateien
├── backups/                      #   Lokale Savegame-Backups
├── scripts/
│   ├── setup_server.sh           #   Ersteinrichtung (root)
│   ├── deploy.sh                 #   Code-Deployment
│   └── manage_bots.sh            #   Start/Stop/Status/Logs
├── systemd/
│   ├── gameserver-bot.service    #   systemd Unit GameServer Bot
│   ├── monitor-bot.service       #   systemd Unit Monitor Bot
│   └── botuser-sudoers           #   sudoers Regeln
├── requirements.txt
├── .gitignore
└── README.md
```

## Alle Slash Commands

### GameServer Bot

| Command | Beschreibung | Berechtigung |
|---------|-------------|--------------|
| `/sat status` | Server-Status + API-Details | Alle |
| `/sat start` | Server starten | Admin |
| `/sat stop` | Server stoppen (5min Countdown) | Admin |
| `/sat restart` | Server neustarten (10min Countdown) | Admin |
| `/sat cancel` | Laufenden Timer abbrechen | Admin |
| `/sat_players` | Online-Spieler anzeigen | Spieler |
| `/sat_kick` | Spieler kicken | Admin |
| `/sat_ban` | Spieler permanent bannen | Admin |
| `/sat_unban` | Ban aufheben | Admin |
| `/sat_broadcast` | Nachricht an alle senden | Spieler |
| `/whitelist add/remove/list` | Whitelist verwalten | Admin |
| `/blacklist add/remove/list` | Blacklist verwalten | Admin |
| `/sat_backup` | Manuelles Backup erstellen | Spieler |
| `/sat_save` | Spielstand speichern | Spieler |
| `/sat_download` | Savegame herunterladen | Spieler |
| `/sat_backups_list` | Backups auflisten | Spieler |
| `/sat_restore` | Backup wiederherstellen | Owner |
| `/sat_settings` | Servereinstellungen anzeigen | Spieler |
| `/sat_playerlimit` | Spielerlimit ändern | Admin |
| `/sat_autosave` | Autosave-Intervall ändern | Admin |
| `/sat_console` | Konsolenbefehl ausführen | Owner |
| `/sat_load` | Savegame laden | Owner |
| `/sat_update` | Server via SteamCMD updaten | Owner |
| `/sat_stats` | Savegame-Statistiken | Spieler |
| `/sat_blueprints_*` | Blueprint Upload/List/Download/Delete | Spieler/Admin |
| `/chatbridge enable/disable/status` | Chat Bridge steuern | Admin |
| `/wordfilter add/remove/list/toggle` | Wortfilter verwalten | Admin |
| `/help` | Alle Commands anzeigen | Alle |
| `/server` | Server-Übersicht + System-Info | Alle |
| `/ping` | Bot-Latenz | Alle |
| `/reload` | Cog zur Laufzeit neuladen | Owner |
| `/timeout` | User temporär stummschalten | Admin |

### Monitor Bot

| Command | Beschreibung | Berechtigung |
|---------|-------------|--------------|
| `/performance` | System-Performance anzeigen | Spieler |
| `/dashboard` | Dashboard-Embed manuell aktualisieren | Admin |
| `/stats [spieler]` | Spieler-Statistiken | Spieler |
| `/report` | Wochenbericht | Spieler |
| `/scheduler` | Scheduler-Status + Konfiguration | Admin |
| `/update_check` | Manuell nach Updates suchen | Admin |

## Konfiguration

### .env (Pflichtfelder)

```bash
DISCORD_TOKEN_MANAGER=     # GameServer Bot Token
DISCORD_TOKEN_WATCHDOG=    # Monitor Bot Token
GUILD_ID=                  # Discord Server ID
OWNER_ID=                  # Deine User ID
ADMIN_ROLE_ID=             # Admin-Rolle ID
SATISFACTORY_ROLE_ID=      # Spieler-Rolle ID
ADMIN_LOG_CHANNEL_ID=      # Admin-Log Kanal
API_TOKEN=                 # Satisfactory API Token
```

### config.json (Feature-Toggles)

Jedes Feature kann einzeln ein-/ausgeschaltet werden:

```json
{
  "features": {
    "chat_bridge": false,
    "daily_restart": true,
    "auto_backup": true,
    "email_notifications": false,
    "onedrive_backup": false
  }
}
```

## Management

```bash
# Bots starten/stoppen
bash scripts/manage_bots.sh start
bash scripts/manage_bots.sh stop
bash scripts/manage_bots.sh restart
bash scripts/manage_bots.sh status

# Logs ansehen
bash scripts/manage_bots.sh logs all
bash scripts/manage_bots.sh logs gameserver
bash scripts/manage_bots.sh logs follow    # Live

# journalctl direkt
journalctl -u gameserver-bot -f
journalctl -u monitor-bot -n 50

# Code-Update deployen
bash scripts/deploy.sh /path/to/discord_bots_v3.tar.gz

# Python-Syntax prüfen
bash scripts/manage_bots.sh validate

# Dependencies updaten
bash scripts/manage_bots.sh update
```

## Berechtigungen

```
Owner (du)     → Alles (console, load, update, reload, restore)
Admin-Rolle    → Steuerung (start, stop, restart, kick, ban, config)
Spieler-Rolle  → Info + Aktionen (status, players, backup, save, stats)
Alle           → Nur Lesen (status, help, server, ping)
```

## Monitoring Features

| Feature | Intervall | Beschreibung |
|---------|-----------|--------------|
| Health Check | 2 Min | API + Prozess-Check, Crash Detection |
| Auto-Restart | Sofort | Nach Crash, 30s Wartezeit, max 5/h |
| Performance | 5 Min | CPU/RAM/Disk mit Schwellwert-Warnungen |
| Dashboard | 10 Min | Auto-Update Embed im Status-Kanal |
| Voice Stats | 5 Min | "SAT: 2/4" und "CPU: 45% | RAM: 62%" |
| Auto-Backup | 6h | Lokal + Optional OneDrive |
| Daily Restart | 04:00 | Nur wenn >12h Uptime, Skip bei Spielern |
| Update Check | 6h | SteamCMD Build-ID Vergleich |
| Player Track | Laufend | Join/Leave, Spielzeit, Wochenbericht |

## Statistiken

- **42 Python-Dateien**
- **8.274 Zeilen Code**
- **2 Bots, 12 Cogs, 18 Module, 4 Utils**
- **~45 Slash Commands**
