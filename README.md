# Discord Game Server Bots v3.0.0

**2-Bot-System fuer Satisfactory + Minecraft Server Management**

Server: Netcup RS 4000 G12 (12 vCores, 32GB RAM, 1TB NVMe) | Ubuntu 22.04 LTS

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
│  /help /server /ping        │    │  Auto-Backup (6h)           │
│                             │    │  Daily Restart (04:00)      │
│                             │    │  Player Tracking + Stats    │
│  /mc status/start/stop      │    │  Update Checker (SteamCMD)  │
│  /mc players/backup/config  │    │  Crash Detection + Restart  │
│  /mc whitelist/command      │    │  Discord + Email Alerts     │
│                             │    │                             │
│  Chat Bridge (MC↔Discord)   │    │  MC Health Check (2 Min)    │
│  Command Logging            │    │  MC Chat Bridge (5s Poll)   │
│                             │    │  MC Auto-Backup + Restart   │
│                             │    │  MC Player Tracking + Stats │
└─────────────────────────────┘    └─────────────────────────────┘
         │                                    │
         └──────────┬─────────────────────────┘
                    │
         ┌──────────▼──────────┐
         │  Satisfactory API   │     ┌─────────────────────┐
         │  systemd Service    │     │  Minecraft Server    │
         │  SteamCMD Updates   │     │  RCON Protocol       │
         └─────────────────────┘     │  Log-Polling         │
                                     │  systemd Services    │
                                     └─────────────────────┘
```

## Unterstuetzte Gameserver

| Server | Typ | Steuerung | Chat-Bridge |
|--------|-----|-----------|-------------|
| Satisfactory | Dedicated Server | HTTP API + systemd | — |
| MC Vanilla/Paper | Paper MC 1.21.4 | RCON + systemd | Log-Polling + RCON |
| MC Better MC | Fabric Modpack (BMC3) | RCON + systemd | Log-Polling + RCON |

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
│   ├── gameserver_bot.py         #   Bot 1: Spielverwaltung (SAT + MC)
│   └── monitor_bot.py            #   Bot 2: Monitoring & Scheduler
├── cogs/                         # Discord Slash-Command Gruppen
│   ├── satisfactory_cog.py       #   /sat start/stop/restart/status/cancel
│   ├── minecraft_cog.py          #   /mc status/start/stop/players/backup/config
│   ├── general_cog.py            #   /help /server /ping /reload /selftest /clear
│   ├── monitor_cog.py            #   /performance /dashboard /stats /report + MC
│   ├── scheduler_cog.py          #   /scheduler /update_check + MC Auto-Tasks
│   ├── maintenance_cog.py        #   /maintenance
│   ├── mod_cog.py                #   /mod install/uninstall/list
│   └── timeout_cog.py            #   /timeout
├── modules/                      # Business Logic
│   ├── minecraft/                #   MC Multi-Server Steuerung
│   │   ├── server.py             #     MinecraftServer (systemd + RCON)
│   │   ├── rcon.py               #     RCON-Client (async, signed ints)
│   │   ├── backup.py             #     World-Backup-Manager
│   │   ├── chat_bridge.py        #     Bidirektionale Chat-Bridge
│   │   ├── settings_backup.py    #     server.properties Backup/Restore
│   │   └── update_checker.py     #     Paper API Update-Check
│   ├── satisfactory/             #   SAT Server, API, Whitelist, Blueprints
│   ├── monitoring/               #   Health Check, Performance, Player Tracker
│   ├── notifications/            #   Discord Notifier, Email Notifier
│   ├── backup/                   #   Backup Manager, OneDrive Backup
│   ├── restart_timer.py          #   Countdown-System mit In-Game Warnungen
│   ├── word_filter.py            #   Wortfilter (partial/exact/regex)
│   ├── anti_spam.py              #   Rate Limiting + Duplikat-Erkennung
│   └── command_logger.py         #   Command Audit Log
├── utils/                        # Hilfsfunktionen
│   ├── config.py                 #   .env + config.json Laden
│   ├── logger.py                 #   Logging Setup
│   ├── formatting.py             #   Embed-Formatierung, Fortschrittsbalken
│   └── permissions.py            #   Berechtigungspruefung
├── config/
│   ├── .env                      #   Secrets (nicht in Git!)
│   ├── .env.example              #   Vorlage (inkl. MC-Variablen)
│   └── config.json               #   Feature-Toggles, Intervalle, Schwellwerte
├── data/                         #   Runtime-Daten (JSON)
├── logs/                         #   Log-Dateien
├── backups/                      #   Lokale Savegame-Backups
├── scripts/
│   ├── setup_server.sh           #   Ersteinrichtung (root)
│   ├── setup_minecraft.sh        #   MC Server-Setup (Java, systemd, UFW)
│   ├── deploy.sh                 #   Code-Deployment
│   └── manage_bots.sh            #   Start/Stop/Status/Logs
├── systemd/
│   ├── gameserver-bot.service    #   systemd Unit GameServer Bot
│   ├── monitor-bot.service       #   systemd Unit Monitor Bot
│   ├── minecraft-vanilla.service #   systemd Unit MC Vanilla/Paper
│   ├── minecraft-bmc.service     #   systemd Unit MC Better MC
│   └── botuser-sudoers           #   sudoers Regeln
├── requirements.txt
├── .gitignore
└── README.md
```

## Alle Slash Commands

### GameServer Bot — Satisfactory

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
| `/sat_playerlimit` | Spielerlimit aendern | Admin |
| `/sat_autosave` | Autosave-Intervall aendern | Admin |
| `/sat_console` | Konsolenbefehl ausfuehren | Owner |
| `/sat_load` | Savegame laden | Owner |
| `/sat_update` | Server via SteamCMD updaten | Owner |
| `/sat_stats` | Savegame-Statistiken | Spieler |
| `/sat_blueprints_*` | Blueprint Upload/List/Download/Delete | Spieler/Admin |

### GameServer Bot — Minecraft

| Command | Beschreibung | Berechtigung |
|---------|-------------|--------------|
| `/mc status [server]` | Server-Status anzeigen | Alle |
| `/mc start <server>` | Server starten (mit Countdown) | Admin |
| `/mc stop <server>` | Server stoppen (mit In-Game Warnung) | Admin |
| `/mc restart <server>` | Server neustarten | Admin |
| `/mc cancel` | Laufenden Timer abbrechen | Admin |
| `/mc players list [server]` | Online-Spieler anzeigen | Spieler |
| `/mc players kick <name>` | Spieler kicken | Admin |
| `/mc players ban <name>` | Spieler bannen | Admin |
| `/mc backup create <server>` | World-Backup erstellen | Spieler |
| `/mc backup list <server>` | Backups auflisten | Spieler |
| `/mc backup restore <server>` | Backup wiederherstellen | Owner |
| `/mc whitelist add/remove/list` | Whitelist verwalten | Admin |
| `/mc command <cmd> [server]` | RCON-Befehl ausfuehren | Owner |
| `/mc say <nachricht>` | Broadcast an alle Spieler | Admin |
| `/mc difficulty <level>` | Schwierigkeitsgrad aendern | Admin |
| `/mc weather <typ>` | Wetter aendern | Admin |
| `/mc time <zeit>` | Tageszeit setzen | Admin |
| `/mc gamemode <modus> <spieler>` | Spielmodus aendern | Admin |
| `/mc config settings [server]` | server.properties anzeigen | Admin |
| `/mc config set <key> <value>` | Einstellung aendern | Owner |
| `/mc config backup [server]` | Config-Backup erstellen | Admin |
| `/mc config restore [server]` | Config wiederherstellen | Owner |
| `/mc config update` | Paper-Update pruefen (nur Vanilla) | Admin |
| `/mc config stats [server]` | World-Statistiken anzeigen | Spieler |

### GameServer Bot — Allgemein

| Command | Beschreibung | Berechtigung |
|---------|-------------|--------------|
| `/help` | Alle Commands anzeigen | Alle |
| `/server` | Server-Uebersicht + System-Info | Alle |
| `/ping` | Bot-Latenz | Alle |
| `/reload` | Cog zur Laufzeit neuladen | Owner |
| `/selftest` | System-Selbsttest (inkl. MC) | Admin |
| `/clear` | Nachrichten loeschen | Admin |
| `/timeout` | User temporaer stummschalten | Admin |

### Monitor Bot

| Command | Beschreibung | Berechtigung |
|---------|-------------|--------------|
| `/performance` | System-Performance anzeigen | Spieler |
| `/dashboard` | Dashboard-Embed manuell aktualisieren | Admin |
| `/stats [spieler]` | Spieler-Statistiken (SAT) | Spieler |
| `/report` | Wochenbericht (SAT) | Spieler |
| `/mcstats [spieler] [server]` | MC-Spieler-Statistiken | Spieler |
| `/mcreport [server]` | MC-Wochenbericht | Spieler |
| `/mccrashlog [server]` | Letzte MC-Crash-Logs anzeigen | Admin |
| `/scheduler` | Scheduler-Status + Konfiguration | Admin |
| `/update_check` | Manuell nach Updates suchen | Admin |

## Konfiguration

### .env (Pflichtfelder)

```bash
# Discord
DISCORD_TOKEN_MANAGER=     # GameServer Bot Token
DISCORD_TOKEN_WATCHDOG=    # Monitor Bot Token
GUILD_ID=                  # Discord Server ID
OWNER_ID=                  # Deine User ID
ADMIN_ROLE_ID=             # Admin-Rolle ID
SATISFACTORY_ROLE_ID=      # Spieler-Rolle ID
ADMIN_LOG_CHANNEL_ID=      # Admin-Log Kanal

# Satisfactory
API_TOKEN=                 # Satisfactory API Token

# Minecraft (pro Server, Prefix MC_{ID}_*)
MC_BMC_SERVICE=minecraft-bmc.service
MC_BMC_RCON_PASSWORD=      # RCON-Passwort
MC_VANILLA_SERVICE=minecraft-vanilla.service
MC_VANILLA_RCON_PASSWORD=  # RCON-Passwort
```

Vollstaendige ENV-Referenz: siehe `config/.env.example`

### config.json (Feature-Toggles)

Jedes Feature kann einzeln ein-/ausgeschaltet werden:

```json
{
  "features": {
    "daily_restart": true,
    "auto_backup": true,
    "email_notifications": false,
    "onedrive_backup": false
  },
  "scheduler": {
    "minecraft": {
      "daily_restart_hour": 4,
      "auto_backup_interval_hours": 6,
      "update_check_interval_hours": 6
    }
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
journalctl -u minecraft-vanilla -f
journalctl -u minecraft-bmc -f

# Code-Update deployen
bash scripts/deploy.sh /path/to/discord_bots_v3.tar.gz

# Python-Syntax pruefen
bash scripts/manage_bots.sh validate
```

## Berechtigungen

```
Owner (du)     → Alles (console, load, update, reload, restore, mc command)
Admin-Rolle    → Steuerung (start, stop, restart, kick, ban, config, whitelist)
Spieler-Rolle  → Info + Aktionen (status, players, backup, save, stats)
Alle           → Nur Lesen (status, help, server, ping)
```

## Monitoring Features

| Feature | Intervall | Beschreibung |
|---------|-----------|--------------|
| Health Check (SAT) | 2 Min | API + Prozess-Check, Crash Detection |
| Health Check (MC) | 2 Min | Prozess + RCON-Check, Downtime-Alerts |
| Auto-Restart | Sofort | Nach Crash, 30s Wartezeit, max 5/h |
| Performance | 5 Min | CPU/RAM/Disk mit Schwellwert-Warnungen |
| Dashboard | 10 Min | Auto-Update Embed (SAT + MC Status) |
| Voice Stats | 5 Min | "SAT: 2/4" und "CPU: 45% | RAM: 62%" |
| Auto-Backup (SAT) | 6h | Lokal + Optional OneDrive |
| Auto-Backup (MC) | 6h | World-Backup pro Server + OneDrive |
| Daily Restart | 04:00 | Nur wenn >12h Uptime, Skip bei Spielern |
| Update Check (SAT) | 6h | SteamCMD Build-ID Vergleich |
| Update Check (MC) | 6h | Paper API Build-Vergleich (nur Vanilla) |
| Player Track | Laufend | Join/Leave, Spielzeit, Wochenbericht |
| Chat Bridge (MC) | 5s | Log-Polling + RCON (bidirektional) |
| Crash Replay | Laufend | Log-Analyse bei Crash (SAT + MC) |

## Server-Infrastruktur

| Dienst | Service-Name | Ports |
|--------|-------------|-------|
| GameServer Bot | `gameserver-bot.service` | — |
| Monitor Bot | `monitor-bot.service` | — |
| Satisfactory | `satisfactory.service` | 7777, 15000, 15777 |
| MC Vanilla/Paper | `minecraft-vanilla.service` | 25565 (Game), 25576 (RCON lokal) |
| MC Better MC | `minecraft-bmc.service` | 25566 (Game), 25575 (RCON lokal) |

## Statistiken

- **60 Python-Dateien**
- **~21.800 Zeilen Code**
- **2 Bots, 8 Cogs, 6 MC-Module, 40 Module gesamt, 4 Utils**
- **~70 Slash Commands**
