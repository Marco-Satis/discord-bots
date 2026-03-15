# Session-Stand: MC-SAT Feature Parity + BMC Server Setup

**Datum:** 19. Februar 2026

---

## 1. MC-SAT Feature Parity — Abgeschlossen & Deployed

Alle 5 Batches der MC-SAT Feature Parity wurden implementiert und auf den Server deployed.

**Commit:** `404a017` — 10 Dateien, 1877 Insertions

### Neue Dateien (2):
- `modules/minecraft/settings_backup.py` — server.properties Backup/Restore
- `modules/minecraft/update_checker.py` — Paper API Update-Check (nur VANILLA)

### Geaenderte Dateien (8):
- `cogs/minecraft_cog.py` — `/mc config` Befehle (settings, set, backup, restore, update, stats)
- `cogs/scheduler_cog.py` — MC Update-Check (6h), Config-Backup (taeglich), Pre-Restart Backup, RCON-Warnings
- `cogs/monitor_cog.py` — `/mcstats`, `/mcreport`, `/mccrashlog` Commands
- `modules/monitoring/stats_tracker.py` — Multi-Server Support (`server_type`, `server_id`)
- `modules/monitoring/crash_replay.py` — `game_type="mc"` mit MC-Error-Keywords
- `modules/monitoring/player_ip_tracker.py` — MC-Regex-Patterns fuer Login/Join/Leave
- `modules/notifications/email_notifier.py` — `server_label` Parameter
- `bots/monitor_bot.py` — MC-Monitoring-Instanzen Init + Status-Embed Erweiterung
- `modules/config_validator.py` — Erweiterte MC-ENV-Checks

### Deployment:
- Via SCP mit SSH-Key (`C:\Users\Marco\.ssh\id_ed25519`)
- Server: `203.0.113.10`, Port `4422`
- Beide Bots erfolgreich gestartet: `gameserver-bot.service` (10 Commands), `monitor-bot.service` (14 Commands)

---

## 2. BMC Server Setup — In Arbeit

### Server-Umgebung:
- **Server:** Netcup RS 4000 G12, 32 GB RAM, Ubuntu 22.04
- **Java:** OpenJDK 21 installiert
- **Vanilla:** Gestoppt und deaktiviert (`minecraft-vanilla.service`)

### Verzeichnisstruktur auf dem Server:
```
/home/minecraft/
├── bettermc/          ← BMC Service WorkingDirectory (existiert, Inhalt unbekannt)
├── better-mc/         ← Neu erstellt, leer
├── vanilla/           ← Vanilla Server (gestoppt)
├── mods/              ← Existiert (Inhalt unbekannt)
└── config/            ← Existiert (Inhalt unbekannt)
```

### BMC Service (`minecraft-bmc.service`):
- `WorkingDirectory=/home/minecraft/bettermc`
- Java-Flags: `-Xms4G -Xmx8G` mit Aikar-Flags
- RCON Port: 25575
- **Hat systemd-Warnungen** — muss repariert werden

### RAM-Planung (32 GB gesamt):
| Dienst | RAM |
|--------|-----|
| SAT Server | 8-12 GB |
| BMC Server | 4-8 GB (-Xmx8G) |
| Vanilla (gestoppt) | 0 GB |
| System + Bots | ~4 GB |

### Better MC Recherche-Ergebnisse:
- **BMC4:** Forge, Minecraft 1.20.1 (beliebteste Version)
- **BMC5:** NeoForge, Minecraft 1.21.1 (neueste Version)
- **RAM-Empfehlung:** 6-8 GB
- **Java:** 17+ (BMC4) / 21+ (BMC5)
- Marco hat das Serverpack bereits heruntergeladen

### Naechste Schritte:
1. Inhalte pruefen: `/home/minecraft/bettermc/`, `/home/minecraft/mods/`, `/home/minecraft/config/`
2. BMC Service-Datei Warnungen beheben
3. Better MC Serverpack nach `/home/minecraft/bettermc/` hochladen/installieren
4. `server.properties` konfigurieren (Port 25566, RCON Port 25575, RCON-Passwort)
5. BMC Server starten und testen
6. Dokumentation aktualisieren

---

## 3. ENV-Variablen fuer MC Multi-Server

Prefix-Pattern: `MC_{SERVER_ID}_*`

### BMC Server:
```env
MC_BMC_SERVICE=minecraft-bmc
MC_BMC_RCON_PASSWORD=<setzen>
MC_BMC_RCON_PORT=25575
MC_BMC_PATH=/home/minecraft/bettermc
MC_BMC_BACKUP_PATH=/home/minecraft/bettermc/backups
MC_BMC_WORLD_PATH=/home/minecraft/bettermc/world
MC_BMC_GAME_CHAT_CHANNEL_ID=<Discord Channel ID>
```

### VANILLA Server:
```env
MC_VANILLA_SERVICE=minecraft-vanilla
MC_VANILLA_RCON_PASSWORD=<setzen>
MC_VANILLA_RCON_PORT=25576
MC_VANILLA_PATH=/home/minecraft/vanilla
MC_VANILLA_BACKUP_PATH=/home/minecraft/vanilla/backups
MC_VANILLA_WORLD_PATH=/home/minecraft/vanilla/world
MC_VANILLA_GAME_CHAT_CHANNEL_ID=<Discord Channel ID>
```

---

## 4. Wichtige Hinweise

- **BMC nutzt Forge/NeoForge** — kein Paper-Update-Check fuer BMC (nur VANILLA)
- **Keine Dateien ohne Genehmigung bearbeiten** — immer zuerst fragen
- **`config/.env` und `config/config.json` NIEMALS anfassen**
- **Git-Commit nach jeder abgeschlossenen Phase**
- **Dokumentation nach BMC-Setup aktualisieren**

---

## 5. Service-Namen Referenz

| Dienst | Service-Name |
|--------|-------------|
| GameServer Bot | `gameserver-bot.service` |
| Monitor Bot | `monitor-bot.service` |
| Satisfactory | (systemd oder manuell) |
| MC Vanilla | `minecraft-vanilla.service` |
| MC BMC | `minecraft-bmc.service` |

---

## 6. SSH/SCP Zugang

```bash
# SSH
ssh -p 4422 -i C:\Users\Marco\.ssh\id_ed25519 root@203.0.113.10

# SCP Upload (Beispiel)
scp -P 4422 -i C:\Users\Marco\.ssh\id_ed25519 <datei> root@203.0.113.10:/path/
```
