# Plan: Deployment + Minecraft-Integration (Phase 14)

## Context

Alle Code-Review-Fixes (Phase 1–3 + B2–B5) sind lokal fertig und committet (11 Commits).
Diese muessen zuerst auf den Server deployed werden. Anschliessend wird die vollstaendige
Minecraft-Integration (Phase 14a–14o) implementiert: 2 Server (Better MC + Vanilla/Paper),
bidirektionale Chat-Bridge, Monitoring, Auto-Restarts.

Die MC-Server sind noch NICHT auf dem Linux-Server installiert — Marco installiert sie separat.
Wir bauen die Bot-Integration so, dass sie aktiviert wird sobald die Server laufen.

---

## Teil 1: Deployment der Review-Fixes

### Schritt 1: Geaenderte Dateien per SCP hochladen

```bash
scp -P 4422 <dateien> marco@203.0.113.10:/home/botuser/Discord_Bots/
```

**Geaenderte Dateien (aus git diff --name-only e23b840..HEAD):**
- `bots/gameserver_bot.py`
- `bots/monitor_bot.py`
- `cogs/general_cog.py`
- `cogs/minecraft_cog.py`
- `cogs/mod_cog.py`
- `cogs/scheduler_cog.py`
- `modules/backup/backup_manager.py`
- `modules/backup/onedrive_backup.py`
- `modules/command_logger.py`
- `modules/config_validator.py`
- `modules/maintenance.py`
- `modules/monitoring/auto_cleanup.py`
- `modules/monitoring/crash_replay.py`
- `modules/monitoring/login_audit.py`
- `modules/monitoring/player_tracker.py`
- `modules/monitoring/savegame_protection.py`
- `modules/monitoring/selftest.py`
- `modules/monitoring/stats_tracker.py`
- `modules/notifications/email_notifier.py`
- `modules/satisfactory/blueprint_manager.py`
- `modules/satisfactory/save_header.py`
- `modules/satisfactory/savegame_analyzer.py`
- `modules/satisfactory/savegame_stats.py`
- `modules/satisfactory/settings_backup.py`
- `utils/formatting.py`
- `utils/permissions.py`
- `docs/REVIEW_BEFUNDE.md` (neu)
- `docs/REVIEW_OFFEN.md` (neu)

**NICHT hochladen:** config/.env, config/config.json, data/, logs/, venv/

### Schritt 2: SSH — Services neustarten

```bash
ssh -p 4422 marco@203.0.113.10
sudo systemctl restart gameserver-bot.service monitor-bot.service
sudo journalctl -u gameserver-bot.service -n 30 --no-pager
sudo journalctl -u monitor-bot.service -n 30 --no-pager
```

### Schritt 3: Verifizieren

- Logs auf Fehler pruefen (Import-Fehler, Startup-Crashes)
- Discord: Bot-Status pruefen (online, Commands erreichbar)

---

## Teil 2: Minecraft-Integration (Phase 14)

### Architektur-Entscheidungen

| Entscheidung | Loesung |
|---|---|
| Anzahl Server | 2 (Better MC + Vanilla/Paper) |
| Bot-Zuordnung | GameServer Bot = Commands, Monitor Bot = Background-Tasks |
| Channel-Struktur | Separate Channels pro Server (eigene ENV-Variablen) |
| Chat-Bridge | Bidirektional (RCON + Log-Polling) |
| Server-Identifikation | Prefix-basiert: `MC_BMC_*` und `MC_VANILLA_*` |
| Timer-Integration | `RestartTimerManager` mit Keys `mc_bmc` / `mc_vanilla` |

### Phase 14a: Multi-Server MinecraftServer Refactoring
**Dateien:** `modules/minecraft/server.py`

- `__init__(self, server_id: str)` — liest ENV mit Prefix `MC_{SERVER_ID}_*`
- PID-Erkennung ueber `systemctl show --property=MainPID` statt psutil-User-Filter
- Fix: `asyncio.get_event_loop()` → `get_running_loop()` (Bug aus Review)
- Fix: Synchrone File-I/O → `run_in_executor`

### Phase 14b: RCON-Client Bugs fixen
**Dateien:** `modules/minecraft/rcon.py`

- Fix: struct format `'<I'` → `'<i'` (signed int)
- Fix: Infinite-Loop in `command()` — Timeout + Max-Iterations
- Reconnect-Logik bei Verbindungsverlust

### Phase 14c: Backup-Manager Multi-Server
**Dateien:** `modules/minecraft/backup.py`

- `__init__(self, server_id, world_path, backup_path)` parametrisiert
- Fix: `asyncio.get_event_loop()` → `get_running_loop()`
- Fix: Synchrones `write_text()` → async

### Phase 14d: API-Adapter fuer RestartTimer
**Dateien:** `modules/minecraft/server.py` (Methode ergaenzen)

- `MinecraftServer` bekommt `run_command(cmd)` als Alias fuer `rcon_command(cmd)`
- Damit kompatibel mit `RestartTimer` (erwartet `api.run_command()`)

### Phase 14e: ENV-Konfiguration + Validator
**Dateien:** `config/.env.example`, `modules/config_validator.py`

Neue ENV-Variablen (pro Server):
```
MC_BMC_SERVICE=minecraft-bmc.service
MC_BMC_PATH=/home/minecraft/bettermc
MC_BMC_WORLD_PATH=/home/minecraft/bettermc/world
MC_BMC_RCON_HOST=127.0.0.1
MC_BMC_RCON_PORT=25575
MC_BMC_RCON_PASSWORD=...
MC_BMC_BACKUP_PATH=/home/minecraft/backups/bmc
MC_BMC_GAME_CHAT_CHANNEL_ID=0
MC_BMC_LOG_PATH=/home/minecraft/bettermc/logs/latest.log

MC_VANILLA_SERVICE=minecraft-vanilla.service
MC_VANILLA_PATH=/home/minecraft/vanilla
MC_VANILLA_WORLD_PATH=/home/minecraft/vanilla/world
MC_VANILLA_RCON_HOST=127.0.0.1
MC_VANILLA_RCON_PORT=25576
MC_VANILLA_RCON_PASSWORD=...
MC_VANILLA_BACKUP_PATH=/home/minecraft/backups/vanilla
MC_VANILLA_GAME_CHAT_CHANNEL_ID=0
MC_VANILLA_LOG_PATH=/home/minecraft/vanilla/logs/latest.log
```

Config-Validator: Minecraft-Checks nur wenn `MC_BMC_SERVICE` oder `MC_VANILLA_SERVICE` gesetzt.

### Phase 14f: Minecraft Cog Refactoring (Multi-Server)
**Dateien:** `cogs/minecraft_cog.py`

- Command-Gruppen: `/mc bmc <cmd>` und `/mc vanilla <cmd>` (oder `/bmc` und `/vanilla`)
- Gemeinsamer Base-Code, Server-Instanz per Parameter
- Bestehende Commands anpassen fuer Multi-Server
- Ban/Whitelist via RCON (`ban`, `pardon`, `whitelist add/remove`)

### Phase 14g: Chat-Bridge (Minecraft → Discord)
**Dateien:** `modules/minecraft/chat_bridge.py` (NEU)

- Log-Polling (analog zu `_poll_player_events` in monitor_bot.py)
- Regex fuer Minecraft-Chat: `\[Server thread/INFO\]: <(\w+)> (.+)`
- Regex fuer Join/Leave: `(\w+) joined the game` / `(\w+) left the game`
- Pro Server eigene Log-Position tracken
- Nachrichten an den konfigurierten Discord-Channel weiterleiten

### Phase 14h: Chat-Bridge (Discord → Minecraft)
**Dateien:** `cogs/minecraft_cog.py` oder `bots/monitor_bot.py`

- `on_message` Event-Listener fuer die MC-Chat-Channels
- Discord-Nachricht → RCON `tellraw` oder `say [Discord] <user>: <msg>`
- Rate-Limiting (max 1 RCON-Call pro Sekunde)

### Phase 14i: Health-Check + Monitoring
**Dateien:** `bots/monitor_bot.py`

- Neuer `@tasks.loop` fuer Minecraft-Server (analog zum Satisfactory health_check_task)
- Prueft: Prozess laeuft? RCON erreichbar? Spielerzahl?
- Downtime-Benachrichtigung an Admin-Channel
- `stats_tracker` erweitern fuer MC-Uptime/Player-Counts

### Phase 14j: Status-Dashboard Embed
**Dateien:** `bots/monitor_bot.py` (`_update_status_embed_impl`)

- Minecraft-Abschnitte im bestehenden Status-Embed
- Pro Server: Online/Offline, Spielerzahl, World-Groesse
- Oder: Separates MC-Status-Embed (eigene Message-ID)

### Phase 14k: Scheduler (Auto-Restart + Auto-Backup)
**Dateien:** `cogs/scheduler_cog.py`

- Bestehenden `scheduler_tick()` erweitern
- `_check_mc_daily_restart(now, server_id)` — analog zu Satisfactory
- `_check_mc_auto_backup(now, server_id)` — World kopieren
- Konfigurierbare Zeiten pro Server

### Phase 14l: systemd Service-Definitionen
**Dateien:** `systemd/minecraft-bmc.service`, `systemd/minecraft-vanilla.service` (NEU)

- Vorlage: bestehende `systemd/gameserver-bot.service`
- User: minecraft, WorkingDirectory pro Server
- ExecStart: java -jar server.jar
- RestartSec, MemoryMax, etc.

### Phase 14m: Player-Tracking fuer Minecraft
**Dateien:** `modules/minecraft/chat_bridge.py` (Join/Leave Events)

- Wiederverwendung von `modules/monitoring/player_tracker.py` Pattern
- Separate Instanz pro MC-Server oder erweiterter PlayerTracker mit Server-Key

### Phase 14n: OneDrive-Backup Integration
**Dateien:** `cogs/scheduler_cog.py`

- Nach MC-Backup: Optional Upload via bestehenden `OneDriveBackup`
- Separater Remote-Pfad pro Server (`MinecraftBackups/BMC/`, `MinecraftBackups/Vanilla/`)

### Phase 14o: Selftest + Dokumentation
**Dateien:** `modules/monitoring/selftest.py`, `docs/`

- Selftest: MC-Server-Status, RCON-Verbindung, Log-Pfad, Backup-Pfad
- Dokumentation aktualisieren

---

## Implementierungsreihenfolge

```
14a → 14b → 14c → 14d → 14e  (Fundament: Server, RCON, Backup, Config)
          ↓
14f → 14g → 14h               (User-facing: Cog, Chat-Bridge)
          ↓
14i → 14j → 14k               (Monitoring: Health-Check, Dashboard, Scheduler)
          ↓
14l → 14m → 14n → 14o         (Extras: systemd, Tracking, Cloud, Docs)
```

**Erster Block (14a–14e):** Fundament — kann sofort implementiert werden, auch ohne laufende MC-Server.

---

## Verifizierung

### Nach Deployment (Teil 1):
- `sudo journalctl -u gameserver-bot.service -n 50` — keine Fehler
- `sudo journalctl -u monitor-bot.service -n 50` — keine Fehler
- Discord: `/sat status`, `/selftest` funktionieren

### Nach Phase 14a–14e:
- Python-Syntax: `python -m py_compile modules/minecraft/server.py`
- Unit-Check: MinecraftServer("bmc") und MinecraftServer("vanilla") instanziieren
- ENV-Validation: ConfigValidator erkennt fehlende MC-Variablen als Warnung

### Nach Phase 14f–14h:
- `/mc bmc status` und `/mc vanilla status` antworten korrekt
- Chat-Bridge: Discord-Nachricht → MC-Chat und MC-Chat → Discord-Channel

### Nach Phase 14i–14o:
- Status-Embed zeigt MC-Server-Status
- Auto-Restart/Backup im Scheduler konfiguriert
- Selftest prueft MC-Subsysteme
