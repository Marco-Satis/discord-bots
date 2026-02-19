# Changelog

Alle relevanten Aenderungen am Discord Bot System werden hier dokumentiert.

---

## [3.0.0] — 2026-02-20

### Hinzugefuegt

- **Minecraft Multi-Server Integration (Phase 14a-14o)**
  - Unterstuetzung fuer 2 MC-Server: Better MC (Forge/NeoForge) + Vanilla/Paper
  - Prefix-basiertes ENV-System (`MC_{SERVER_ID}_*`) fuer beliebig viele Server
  - `modules/minecraft/server.py` — MinecraftServer-Klasse mit systemd-Steuerung, RCON, Uptime-Tracking
  - `modules/minecraft/rcon.py` — Async RCON-Client mit signed ints, Reconnect, Bounded Loops
  - `modules/minecraft/backup.py` — World-Backup-Manager mit async I/O und automatischem Cleanup
  - `modules/minecraft/chat_bridge.py` — Bidirektionale Chat-Bridge (Log-Polling + RCON)
  - `modules/minecraft/settings_backup.py` — server.properties Backup/Restore
  - `modules/minecraft/update_checker.py` — Paper API Update-Check (nur Vanilla)

- **Minecraft Slash Commands (~25 neue Commands)**
  - `/mc status/start/stop/restart/cancel` — Server-Steuerung mit Countdown und In-Game-Warnungen
  - `/mc players list/kick/ban` — Spieler-Verwaltung via RCON
  - `/mc backup create/list/restore` — World-Backup-Management
  - `/mc whitelist add/remove/list` — Whitelist-Verwaltung via RCON
  - `/mc command` — Direkte RCON-Befehlsausfuehrung (Owner)
  - `/mc say/difficulty/weather/time/gamemode` — Admin-Befehle
  - `/mc config settings/set/backup/restore/update/stats` — Konfiguration + World-Stats
  - `/mcstats`, `/mcreport`, `/mccrashlog` — MC-Statistiken und Crash-Logs (Monitor Bot)

- **Minecraft Monitoring (Monitor Bot)**
  - Health-Check alle 2 Minuten (Prozess + RCON), Downtime-Alerts nach 6 Minuten
  - Chat-Bridge: Log-Polling alle 5 Sekunden, Discord→MC via RCON
  - Auto-Backup alle 6 Stunden pro Server (mit save-all vor Backup)
  - Daily Restart um 04:00 (mit In-Game-Warnungen via RCON)
  - Player-Tracking: Separate Instanz pro MC-Server
  - Status-Dashboard: MC-Server-Status im bestehenden Embed
  - Update-Check via Paper API (alle 6 Stunden, nur Vanilla)
  - Crash-Replay mit MC-spezifischen Error-Keywords

- **MC-SAT Feature Parity (Commit 404a017)**
  - StatsTracker: Multi-Server Support (`server_type`, `server_id`)
  - CrashReplay: `game_type="mc"` mit MC-Error-Keywords
  - PlayerIPTracker: MC-Regex-Patterns fuer Login/Join/Leave
  - EmailNotifier: `server_label` Parameter
  - ConfigValidator: Erweiterte MC-ENV-Checks

- **systemd Service-Definitionen**
  - `minecraft-vanilla.service` — Paper MC mit Aikar-Flags, 2-4G RAM, RCON Graceful-Shutdown
  - `minecraft-bmc.service` — Better MC mit Aikar-Flags, 4-8G RAM, Resource-Limits
  - Setup-Scripte: `setup_minecraft.sh`, `setup_minecraft_fix.sh`

### Sicherheit

- Mention-Injection-Schutz: `AllowedMentions.none()` fuer alle MC→Discord Nachrichten
- RCON-Injection-Schutz: Sanitisierung aller Discord→MC Nachrichten
- Path-Traversal-Schutz: `.resolve()` + Prefix-Check in Backup restore/delete
- systemctl Action-Whitelist: `_ALLOWED_ACTIONS` frozenset
- Per-Server Timer-Keys (verhindert globale Blockierung)

### Behoben (Code-Review Phase 6)

- 3 Critical: Mention-Injection, globale Timer-Blockierung, Tasks ohne MC-Server
- 12 Warnings: Fehlende Instanzen, sync I/O, process_commands Blockierung, u.a.
- 1 Bug: `close_all_sessions()` leerte `_online_players` Set nicht (Endlos-Spam)

---

## [2.2.0] — 2026-02-18

### Behoben

- **12 kritische Fehler:** Shell-Injection in optimizer.py, Command-Injection in server.py, fehlende Imports (asyncio), Namespace-Iteration-Bug, fehlender await bei crash_replay.capture(), OWNER_ID ohne Default, Shutdown-Cleanup unvollstaendig
- **8 Logik-Fehler:** Nested Event Loop in maintenance.py, Race Condition in savegame_analyzer.py, Rate-Limiting Off-by-one in anti_spam.py, None-Safety in mod_manager.py, Path-Traversal in blueprint_manager.py
- **6 Architektur-Fixes:** sudoers Haertung (bash -c entfernt, Wildcards eingeschraenkt), bot-watchdog.service vervollstaendigt, drop-caches.sh Script erstellt

### Verbessert

- Type Hints fuer 36 Dateien vervollstaendigt (Python 3.9-kompatibel)
- 18 bare Exceptions durch spezifische Exceptions ersetzt
- 13 Cog Error Handler vereinheitlicht (CheckFailure → ephemeral, Logging mit exc_info)
- Dokumentation: REVIEW_BEFUNDE.md, REVIEW_OFFEN.md erstellt

---

## [2.0.0] — 2026-02

### Hinzugefuegt

- Satisfactory Blueprint-Management (Upload/Download/List/Delete)
- Whitelist/Blacklist-System
- Chat-Bridge (Satisfactory ↔ Discord)
- Word Filter + Anti-Spam
- Savegame-Analyse und -Statistiken
- SteamCMD Update-Checker
- OneDrive Cloud-Backup via rclone
- E-Mail-Benachrichtigungen
- Player-Tracking mit Wochenberichten
- Crash-Replay (Log-Analyse)
- Performance-Monitoring mit Schwellwert-Warnungen
- Voice-Channel Stats
- Command Audit-Logging

---

## [1.0.0] — 2026-01

### Hinzugefuegt

- Initiales 2-Bot-System (GameServer Bot + Monitor Bot)
- Satisfactory Server-Steuerung (start/stop/restart/status)
- Health Check mit Auto-Restart
- Einfaches Backup-System
- Dashboard-Embed
- Daily Restart (04:00)
