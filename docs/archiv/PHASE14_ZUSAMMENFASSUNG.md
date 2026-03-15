# Phase 14: Minecraft-Integration — Vollstaendige Zusammenfassung

> **Datum:** 19. Februar 2026
> **Status:** Code fertig, Review fertig, Deployed, MC-Server installiert (noch nicht gestartet)
> **Branch:** `master` (lokal)

---

## Uebersicht

Vollstaendige Minecraft-Integration fuer 2 Server (Better MC + Vanilla/Paper)
mit bidirektionaler Chat-Bridge, Monitoring, Auto-Restarts und Backups.

**18 Commits** auf `master`:

```
09645d0 [Fix] Player-Tracker: close_all_sessions leert jetzt _online_players Set
9385c9c [Review] Schritt C: CRITICAL + WARNING Befunde behoben
52ba4eb [Phase 14o] Selftest MC-Checks + B1 als behoben markiert
798c541 [Phase 14m] Player-Tracking fuer Minecraft
81670cb [Phase 14l] systemd Service-Definitionen fuer Minecraft
79904f3 [Phase 14k] Minecraft Scheduler: Auto-Restart + Auto-Backup
ad1d32c [Phase 14j] Minecraft Status im Dashboard-Embed
1244000 [Phase 14i] Minecraft Health-Check Task
8d4c21c [Phase 14h] Chat-Bridge Integration (Discord→MC + Callbacks)
70d959c [Phase 14g] Chat-Bridge MC→Discord (Log-Polling)
a6c6889 [Phase 14f] Minecraft Cog Multi-Server Refactoring
d348242 [Phase 14e] ENV-Konfiguration + Validator
ec6e58a [Phase 14c] Minecraft Backup-Manager Multi-Server
708fdf6 [Phase 14b] RCON-Client Bugfixes
09a64f1 [Phase 14a] Multi-Server MinecraftServer + Bot-Integration
aafb7b0 [Docs] Minecraft-Integrationsplan (Phase 14a-14o) erstellt
0344a66 [Docs] REVIEW_OFFEN.md aktualisiert — B2-B5 als behoben markiert
4784432 [Fix] B2-B5: Logging, Race Conditions, Shutdown-Loop behoben
```

---

## Deployment-Status

### Bot-Code (auf Server deployed)

| Datei | Status | Datum |
|---|---|---|
| `bots/gameserver_bot.py` | Deployed | 19.02.2026 12:53 |
| `bots/monitor_bot.py` | Deployed | 19.02.2026 12:53 |
| `cogs/minecraft_cog.py` | Deployed | 19.02.2026 12:53 |
| `cogs/scheduler_cog.py` | Deployed | 19.02.2026 12:53 |
| `modules/minecraft/server.py` | Deployed | 19.02.2026 12:53 |
| `modules/minecraft/rcon.py` | Deployed | 19.02.2026 12:53 |
| `modules/minecraft/backup.py` | Deployed | 19.02.2026 12:53 |
| `modules/minecraft/chat_bridge.py` | Deployed | 19.02.2026 12:53 |
| `modules/config_validator.py` | Deployed | 19.02.2026 12:53 |
| `modules/monitoring/selftest.py` | Deployed | 19.02.2026 12:53 |
| `modules/monitoring/player_tracker.py` | Deployed | 19.02.2026 13:00 |
| `config/.env.example` | Deployed | 19.02.2026 12:53 |

**Beide Bots laufen fehlerfrei** (`gameserver-bot.service` + `monitor-bot.service` = active).

### Minecraft-Server-Infrastruktur (auf Server installiert)

| Komponente | Status | Details |
|---|---|---|
| Java 21 | Installiert | OpenJDK 21.0.10 |
| minecraft User | Vorhanden | uid=1002, gid=1003 |
| `/home/minecraft/vanilla/` | Erstellt | Paper MC 1.21.4 Build 209, server.jar + eula.txt + server.properties |
| `/home/minecraft/bettermc/` | Erstellt | eula.txt + server.properties (Modpack fehlt noch) |
| `/home/minecraft/backups/vanilla/` | Erstellt | Leer |
| `/home/minecraft/backups/bmc/` | Erstellt | Leer |
| `minecraft-vanilla.service` | Installiert, inaktiv | Aikar-Flags, 2-4G RAM, RCON Port 25576 |
| `minecraft-bmc.service` | Installiert, inaktiv | Aikar-Flags, 4-8G RAM, RCON Port 25575 |
| `minecraft.service` (alt) | Deaktiviert | Ehemaliger Single-Server unter /home/minecraft/server/ |
| `rcon-cli` | Installiert | v0.10.3 (fuer Graceful-Shutdown via ExecStop) |
| Sudoers botuser | Erweitert | NOPASSWD fuer start/stop/restart/status/is-active/show beider MC-Services |
| UFW Port 25565 | Offen | Minecraft Vanilla |
| UFW Port 25566 | Offen | Minecraft Better MC |
| RCON Ports 25575/25576 | Nur lokal | Kein UFW allow (standardmaessig geblockt) |

---

## Server-Hardware (Netcup RS 4000 G12)

| Ressource | Wert |
|---|---|
| CPU | 12 Kerne |
| RAM | 31 GB |
| Disk | ~950 GB frei |
| OS | Ubuntu (mit systemd) |
| IP | 203.0.113.10 |
| SSH-Port | 4422 |

### RAM-Aufteilung (geplant)

| Dienst | Min | Max | MemoryMax |
|---|---|---|---|
| Vanilla/Paper | 2 GB | 4 GB | 6 GB |
| Better MC | 4 GB | 8 GB | 10 GB |
| Satisfactory | — | — | bestehend |
| Discord Bots | ~200 MB | ~500 MB | — |
| System + Reserve | ~8 GB | | |
| **Gesamt** | | | **~31 GB** |

---

## systemd Services (Details)

### minecraft-vanilla.service

```ini
[Service]
User=minecraft
WorkingDirectory=/home/minecraft/vanilla
ExecStart=/usr/bin/java -Xms2G -Xmx4G [Aikar-Flags] -jar server.jar nogui
ExecStop=/usr/bin/rcon-cli --host 127.0.0.1 --port 25576 --password ... stop
MemoryMax=6G
CPUQuota=200%
Restart=on-failure (max 3x in 600s)
```

### minecraft-bmc.service

```ini
[Service]
User=minecraft
WorkingDirectory=/home/minecraft/bettermc
ExecStart=/usr/bin/java -Xms4G -Xmx8G [Aikar-Flags] -jar server.jar nogui
ExecStop=/usr/bin/rcon-cli --host 127.0.0.1 --port 25575 --password ... stop
MemoryMax=10G
CPUQuota=200%
Restart=on-failure (max 3x in 600s)
```

### Aikar JVM-Flags (beide Server)

```
-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200
-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch
-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M
-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4
-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90
-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32
-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1
```

---

## Netzwerk-Konfiguration

| Dienst | Port | Protokoll | Zugang |
|---|---|---|---|
| Vanilla/Paper | 25565 | TCP | Oeffentlich (UFW allow) |
| Better MC | 25566 | TCP | Oeffentlich (UFW allow) |
| RCON Vanilla | 25576 | TCP | Nur lokal (127.0.0.1) |
| RCON Better MC | 25575 | TCP | Nur lokal (127.0.0.1) |

---

## Implementierte Phasen (Code)

### Phase 14a: Multi-Server MinecraftServer
**Datei:** `modules/minecraft/server.py`, `bots/gameserver_bot.py`

- `MinecraftServer` Klasse mit ENV-Prefix `MC_{SERVER_ID}_*`
- PID-Erkennung via `systemctl show --property=MainPID`
- Uptime via monotonischen Timestamps
- `run_command()` Alias fuer RestartTimer-Kompatibilitaet
- `enabled` Property (aktiviert wenn Service-Name gesetzt)
- Async `get_properties()` / `set_property()` via Executor
- Bot-Integration: `bot.mc_servers` Dict, `bot.mc_backup_mgrs`, `bot.mc_mod_mgrs`

### Phase 14b: RCON-Client Bugfixes
**Datei:** `modules/minecraft/rcon.py`

- **Fix:** struct Format `'<I'` → `'<i'` (signed int fuer Auth-Failure -1)
- **Fix:** Laengenberechnung korrigiert (fehlende Pad-Null)
- **Fix:** Receive liest exakt `length` Bytes (war off-by-one)
- **Fix:** Infinite-Loop → Bounded Loop (max 64 Pakete, Short-Timeout)
- Reconnect-Methode hinzugefuegt
- Request-ID Overflow-Schutz
- Paketgroesse Sanity-Check

### Phase 14c: Backup-Manager Multi-Server
**Datei:** `modules/minecraft/backup.py`

- Konstruktor: `(savegame_path, backup_path, max_backups, server_id)`
- Alle I/O async via `asyncio.get_running_loop().run_in_executor()`
- Automatisches Cleanup alter Backups (`_cleanup_old_backups`)
- `list_backups()` und `delete_backup()` jetzt async
- `session.lock` in Ignore-Patterns
- Path-Traversal-Schutz in restore/delete (`.resolve()` + Prefix-Check)

### Phase 14e: ENV-Konfiguration + Validator
**Dateien:** `config/.env.example`, `modules/config_validator.py`

Neue ENV-Variablen (pro Server):
```
MC_BMC_SERVICE=minecraft-bmc.service
MC_BMC_DISPLAY_NAME=Better MC
MC_BMC_PATH=/home/minecraft/bettermc
MC_BMC_WORLD_PATH=/home/minecraft/bettermc/world
MC_BMC_RCON_HOST=127.0.0.1
MC_BMC_RCON_PORT=25575
MC_BMC_RCON_PASSWORD=<RCON-Passwort>
MC_BMC_BACKUP_PATH=/home/minecraft/backups/bmc
MC_BMC_GAME_CHAT_CHANNEL_ID=0
MC_BMC_LOG_PATH=/home/minecraft/bettermc/logs/latest.log

MC_VANILLA_SERVICE=minecraft-vanilla.service
MC_VANILLA_DISPLAY_NAME=Vanilla/Paper
MC_VANILLA_PATH=/home/minecraft/vanilla
MC_VANILLA_WORLD_PATH=/home/minecraft/vanilla/world
MC_VANILLA_RCON_HOST=127.0.0.1
MC_VANILLA_RCON_PORT=25576
MC_VANILLA_RCON_PASSWORD=<RCON-Passwort>
MC_VANILLA_BACKUP_PATH=/home/minecraft/backups/vanilla
MC_VANILLA_GAME_CHAT_CHANNEL_ID=0
MC_VANILLA_LOG_PATH=/home/minecraft/vanilla/logs/latest.log
```

Config-Validator: MC-Checks nur wenn `MC_{ID}_SERVICE` gesetzt.

### Phase 14f: Minecraft Cog (alle /mc Befehle)
**Datei:** `cogs/minecraft_cog.py`

Command-Struktur:
```
/mc status [server]                     — Server-Status (Alle)
/mc start|stop|restart|cancel [server]  — Server-Steuerung (Admin)
/mc players list|kick|ban [server]      — Spieler-Verwaltung
/mc backup create|list|restore [server] — Backup-Verwaltung
/mc whitelist add|remove|list [server]  — Whitelist (Admin)
/mc command <cmd> [server]              — RCON ausfuehren (Owner)
/mc say|difficulty|weather|time|gamemode — Admin-Befehle
```

- Server-Autocomplete zeigt nur aktivierte Server
- Auto-Auswahl bei nur einem Server
- B1-Fix: `on_complete` Callback, kein zweiter Aufruf nach Timer
- Ban/Pardon/Whitelist via RCON
- RestartTimerManager mit Key `mc_{server_id}`
- Per-Server Timer-Check (nicht global blockierend)

### Phase 14g: Chat-Bridge MC→Discord
**Datei:** `modules/minecraft/chat_bridge.py` (NEU)

- Regex-Patterns fuer Chat, Join, Leave, Advancements, Deaths
- Log-Polling mit Position-Tracking und Rotation-Erkennung
- Callback-basierte Architektur
- Rate-Limited Discord→MC via RCON (`send_to_minecraft`)
- RCON-Injection-Schutz (Sanitisierung)

### Phase 14h: Chat-Bridge Discord→MC
**Datei:** `bots/monitor_bot.py`

- MC-Imports und Initialisierung
- `mc_chat_bridge_task` (5s Polling)
- `on_message` Event-Handler mit Channel-Map
- `_build_mc_chat_channel_map()` beim Start
- `process_commands` wird immer aufgerufen (auch nach MC-Nachricht)

### Phase 14i: Health-Check
**Datei:** `bots/monitor_bot.py`

- `mc_health_check_task` (120s Loop)
- Downtime-Benachrichtigung nach 6 Min offline
- Recovery-Benachrichtigung bei Wiedererreichbarkeit
- Pro-Server Tracking (`_mc_consecutive_offline`, `_mc_downtime_notified`)

### Phase 14j: Status-Dashboard Embed
**Datei:** `bots/monitor_bot.py`

- Minecraft-Abschnitte im bestehenden Status-Embed
- Pro Server: Online/Offline, Spielerzahl, World-Groesse
- Nur angezeigt wenn MC-Server konfiguriert

### Phase 14k: Scheduler (Auto-Restart + Auto-Backup)
**Datei:** `cogs/scheduler_cog.py`

- `_check_mc_daily_restart()` — In-Game Warnungen (2min, 1min, jetzt)
- `_check_mc_auto_backup()` — save-all RCON vor Backup
- Konfigurierbar via `config.json` → `scheduler.minecraft.*`
- Optionaler OneDrive-Upload nach Backup
- Pro-Server State: `_mc_last_restart`, `_mc_last_backup`

### Phase 14l: systemd Service-Definitionen
**Dateien:** `systemd/minecraft-bmc.service`, `systemd/minecraft-vanilla.service` (NEU)

- User: minecraft, Group: minecraft
- Java Startbefehl mit Aikar-Flags + konfigurierbarem Speicher
- Graceful Shutdown via rcon-cli
- Resource Limits (MemoryMax, CPUQuota, LimitNOFILE)
- Restart on-failure mit Limits

### Phase 14m: Player-Tracking
**Datei:** `bots/monitor_bot.py`

- Separate `PlayerTracker`-Instanz pro MC-Server
- Join/Leave Callbacks aktualisieren Tracker
- Daten in `data/mc_{server_id}/`

### Phase 14o: Selftest + Docs
**Datei:** `modules/monitoring/selftest.py`

- `_test_minecraft()`: Server-Status, RCON, Log-Pfad, Backup-Pfad
- Pro konfiguriertem Server eigene Test-Ergebnisse
- B1 als behoben in REVIEW_OFFEN.md markiert

---

## Code-Review (Schritt C)

Zwei parallele Review-Agents haben alle MC-Dateien + bestehende Dateien geprueft.

### Behobene CRITICAL-Befunde (3)

| # | Befund | Fix |
|---|--------|-----|
| C1 | @everyone/@here Mention-Injection aus MC-Chat | `AllowedMentions.none()` fuer alle MC→Discord `channel.send()` |
| C2 | Timer `has_active` blockiert ALLE Server global | Per-Server Timer-Key Check (`_timers.get(timer_key)`) |
| C3 | MC-Tasks starten auch ohne MC-Server | Guard in `on_ready`: nur starten wenn `mc_servers` nicht leer |

### Behobene WARNING-Befunde (12)

| # | Befund | Fix |
|---|--------|-----|
| W1 | `mc_backup_mgrs` fehlt auf Monitor Bot | Erstellt auf Monitor Bot (Scheduler Auto-Backups funktionieren) |
| W2 | `server_id` nicht an BackupManager uebergeben | Explizit uebergeben in beiden Bots |
| W3 | stats_tracker Pollution (MC in Satisfactory-Daten) | MC-Spielerzahlen nicht mehr in Satisfactory stats_tracker |
| W4 | Path-Traversal in Backup restore/delete | `.resolve()` + Prefix-Check gegen backup_path |
| W5 | `on_message` blockiert `process_commands` | process_commands immer aufrufen |
| W6 | `get_world_size()` synchron/blockierend | Async via `run_in_executor` |
| W7 | Sync `shutil.rmtree` im Error-Handler | Async via `run_in_executor` |
| W8 | `mc_mod_mgrs` fragiles hasattr-Pattern | Dict vor der Loop initialisieren |
| W9 | Fehlende systemctl Action-Whitelist | `_ALLOWED_ACTIONS` frozenset |
| W10 | Subprocess nicht gekillt bei Timeout | `proc.kill()` + `proc.wait()` |
| W11 | `int()` Cast ohne Fehlerbehandlung | Dokumentiert (Absturz bei falschem ENV-Wert ist gewollt) |
| W12 | Restore prueft nicht ob Server gestoppt | Cog prueft dies bereits vor Aufruf |

### Behobener Bug (Post-Review)

| # | Befund | Fix |
|---|--------|-----|
| B6 | `close_all_sessions()` leert `_online_players` Set nicht | `self._online_players.clear()` hinzugefuegt — verhinderte Endlos-Spam im Log |

### Akzeptierte INFO-Befunde (kein Fix noetig)

- Stub `online_players` Property in chat_bridge.py (Player-Tracker verwaltet)
- Per-Instance Rate-Limit (korrekt bei 1 Bridge pro Server)
- Connection-per-Command RCON (einfachste korrekte Implementierung)
- File-Locking bei server.properties (Aenderungen selten + Server gestoppt)
- Multi-Packet Timeout-Heuristik (funktioniert fuer typische MC-Commands)
- Lowercase generics (Python 3.10+ auf Server)

---

## Architektur-Entscheidungen

| Entscheidung | Loesung |
|---|---|
| Anzahl Server | 2 (Better MC + Vanilla/Paper) |
| Bot-Zuordnung | GameServer Bot = Commands, Monitor Bot = Background-Tasks |
| Channel-Struktur | Separate Channels pro Server (eigene ENV-Variablen) |
| Chat-Bridge | Bidirektional (RCON + Log-Polling, 5s Intervall) |
| Server-Identifikation | Prefix-basiert: `MC_BMC_*` und `MC_VANILLA_*` |
| Timer-Integration | `RestartTimerManager` mit Keys `mc_bmc` / `mc_vanilla` |
| Player-Tracking | Separate `PlayerTracker`-Instanz pro MC-Server |
| Backups | `MinecraftBackupManager`-Instanz pro Server auf beiden Bots |
| JVM-Flags | Aikar's G1GC-Flags (aus bestehendem minecraft.service uebernommen) |
| Graceful-Shutdown | `rcon-cli stop` via ExecStop (statt SIGTERM) |
| Sicherheit | Mention-Injection-Schutz, RCON-Injection-Schutz, Path-Traversal-Schutz |

---

## Geaenderte Dateien (komplett)

### Neue Dateien
- `modules/minecraft/chat_bridge.py` — Bidirektionale Chat-Bridge
- `systemd/minecraft-bmc.service` — systemd Service Better MC
- `systemd/minecraft-vanilla.service` — systemd Service Vanilla/Paper
- `scripts/setup_minecraft.sh` — Server-Setup-Script
- `scripts/setup_minecraft_fix.sh` — Fix-Script fuer rcon-cli (v0.10.3)
- `docs/PHASE14_ZUSAMMENFASSUNG.md` — Diese Datei

### Geaenderte Dateien
- `modules/minecraft/server.py` — Komplett neu: Multi-Server, async, Action-Whitelist
- `modules/minecraft/rcon.py` — Komplett neu: Signed ints, Bounded loops, Reconnect
- `modules/minecraft/backup.py` — Komplett neu: Multi-Server, async I/O, Path-Traversal-Schutz
- `modules/config_validator.py` — MC-Checks hinzugefuegt (nur wenn SERVICE gesetzt)
- `cogs/minecraft_cog.py` — Komplett neu: Multi-Server Commands, Per-Server Timer
- `cogs/scheduler_cog.py` — MC Auto-Restart + Auto-Backup hinzugefuegt
- `bots/gameserver_bot.py` — MC Multi-Server Init, server_id, mc_mod_mgrs
- `bots/monitor_bot.py` — Chat-Bridge, Health-Check, Status-Embed, Player-Tracking, Mention-Schutz
- `modules/monitoring/selftest.py` — MC-Tests hinzugefuegt
- `modules/monitoring/player_tracker.py` — close_all_sessions Bug gefixt
- `config/.env.example` — MC ENV-Variablen dokumentiert

---

## SSH-Zugang + Deployment-Infos

| Parameter | Wert |
|---|---|
| Server-IP | 203.0.113.10 |
| SSH-Port | 4422 |
| botuser SSH-Key | `C:/Users/Marco/.ssh/.ssh_botuser_key` |
| Bot-Dateien | `/home/botuser/Discord_Bots/` |
| MC-Dateien | `/home/minecraft/vanilla/` + `/home/minecraft/bettermc/` |

### Deployment-Befehl (SCP)
```bash
scp -P 4422 -i "C:/Users/Marco/.ssh/.ssh_botuser_key" <datei> botuser@203.0.113.10:/home/botuser/Discord_Bots/<pfad>
```

### Bot-Neustart
```bash
ssh -p 4422 -i "C:/Users/Marco/.ssh/.ssh_botuser_key" botuser@203.0.113.10 \
  "sudo /usr/bin/systemctl restart gameserver-bot.service monitor-bot.service"
```

### botuser Sudo-Rechte (NOPASSWD)
- `systemctl start|stop|restart` fuer: `satisfactory.service`, `gameserver-bot.service`, `monitor-bot.service`, `minecraft-vanilla.service`, `minecraft-bmc.service`
- `systemctl status|is-active|show` fuer: `minecraft-vanilla.service`, `minecraft-bmc.service`
- `/usr/sbin/ufw`
- `/usr/local/bin/drop-caches.sh`

---

## Naechste Schritte (fuer Marco)

### Vor dem ersten Start

1. **RCON-Passwoerter aendern** — Aktuell Platzhalter!
   - In `/etc/systemd/system/minecraft-vanilla.service` (ExecStop-Zeile)
   - In `/etc/systemd/system/minecraft-bmc.service` (ExecStop-Zeile)
   - In `/home/minecraft/vanilla/server.properties` (`rcon.password=`)
   - In `/home/minecraft/bettermc/server.properties` (`rcon.password=`)
   - Danach: `sudo systemctl daemon-reload`

2. **Better MC Modpack hochladen** nach `/home/minecraft/bettermc/`
   - `server.jar` + `mods/` + weitere Modpack-Dateien
   - `eula.txt` und `server.properties` sind bereits vorhanden

### Vanilla/Paper starten

```bash
sudo systemctl start minecraft-vanilla
sudo journalctl -fu minecraft-vanilla    # Logs beobachten
```

Beim ersten Start generiert Paper die World + konfiguriert sich selbst.

### Better MC starten (nachdem Modpack hochgeladen)

```bash
sudo systemctl start minecraft-bmc
sudo journalctl -fu minecraft-bmc
```

### Bot-Integration aktivieren

3. **ENV-Variablen setzen** in `/home/botuser/Discord_Bots/config/.env`:

```bash
# Vanilla/Paper
MC_VANILLA_SERVICE=minecraft-vanilla.service
MC_VANILLA_DISPLAY_NAME=Vanilla/Paper
MC_VANILLA_PATH=/home/minecraft/vanilla
MC_VANILLA_WORLD_PATH=/home/minecraft/vanilla/world
MC_VANILLA_RCON_HOST=127.0.0.1
MC_VANILLA_RCON_PORT=25576
MC_VANILLA_RCON_PASSWORD=<dein-echtes-rcon-passwort>
MC_VANILLA_BACKUP_PATH=/home/minecraft/backups/vanilla
MC_VANILLA_LOG_PATH=/home/minecraft/vanilla/logs/latest.log
MC_VANILLA_GAME_CHAT_CHANNEL_ID=0

# Better MC
MC_BMC_SERVICE=minecraft-bmc.service
MC_BMC_DISPLAY_NAME=Better MC
MC_BMC_PATH=/home/minecraft/bettermc
MC_BMC_WORLD_PATH=/home/minecraft/bettermc/world
MC_BMC_RCON_HOST=127.0.0.1
MC_BMC_RCON_PORT=25575
MC_BMC_RCON_PASSWORD=<dein-echtes-rcon-passwort>
MC_BMC_BACKUP_PATH=/home/minecraft/backups/bmc
MC_BMC_LOG_PATH=/home/minecraft/bettermc/logs/latest.log
MC_BMC_GAME_CHAT_CHANNEL_ID=0
```

4. **Discord-Channels erstellen** fuer MC-Chat (pro Server)
   - Channel-IDs in `MC_VANILLA_GAME_CHAT_CHANNEL_ID` und `MC_BMC_GAME_CHAT_CHANNEL_ID` eintragen
   - `0` = Chat-Bridge deaktiviert fuer diesen Server

5. **Bot-Services neustarten** damit ENV-Variablen geladen werden:
```bash
sudo systemctl restart gameserver-bot.service monitor-bot.service
```

### Testen

6. **Discord-Commands testen:**
   - `/mc status` — Zeigt Status beider Server
   - `/mc start vanilla` — Vanilla starten (falls nicht schon an)
   - `/selftest` — Prueft MC-Subsysteme (RCON, Logs, Backups)

7. **Chat-Bridge testen** (wenn Channel-IDs gesetzt):
   - Nachricht im Discord-MC-Channel schreiben → erscheint im MC-Chat
   - Im MC chatten → erscheint im Discord-Channel

8. **Backups testen:**
   - `/mc backup create vanilla` — Erstellt World-Backup
   - `/mc backup list vanilla` — Zeigt vorhandene Backups
