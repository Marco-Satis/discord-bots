# Discord Bot System — Abgeschlossene Features & Arbeiten

> **Stand:** 2. Juni 2026 | **Aktuelle Version:** v4.4.0
> **Server:** Netcup RS 4000 G12 (12 vCores, 31 GB RAM, 1 TB NVMe, Ubuntu 22.04)
> **Python:** 3.10.12 | **Discord.py:** 2.x | **DB:** SQLite (WAL-Modus) | **Web:** FastAPI + Jinja2

---

## 0. v4.4.0 — Konsolidierung master↔main (+ Server-Quelle) — 2026-06-02

Git-seitig komplett, gepusht (`origin/main` = `f93eac9`, privates Repo). **Server-Redeploy 2026-06-02 erledigt** — Server läuft v4.4.0 (Deploy-Script grün, remote verifiziert: 4/4 Services active, 0 Errors, Dashboard ok; Backup `backup_pre_v440_1780401693.tar.gz`).

- **3-Wege-Merge** der divergierten Linien (`main` V5/Updater/manual_stop ↔ `master` ~40 Module) via Vorfahr `9c680a5`. 15 Konflikte gelöst, keine Funktion verloren — orthogonale Features kombiniert:
  - `db_manager.py`: Read-Pool (main) **+** Cross-Prozess-Write-Retry (master) beide aktiv.
  - `reaction_roles_cog.py`: `track_task` GC-Schutz (main) **+** Panel-Lock-Cleanup-Loop-Start (master).
  - `pipeline_approval_cog.py`: master-Superset (Approve/Dismiss/**Recat**).
  - Monitoring (`health_checker`/`service_watchdog`/`package_checker`): main manual_stop + 0-Update-Filter behalten.
- **Frontend:** main-V5 autoritativ (master-Alt-Design verworfen); verwaisten Dashboard-`Events-Clear`-Button nachverdrahtet; Mods-Daten in V5 im Updates-Tab integriert. `server_detail.py`→`server_detail_route.py`-Rename sauber.
- **master-Module dazu:** `scripts/rcon_op.py`, `web/tools/build_css.py`, 6 Server-Test-Files, Basecoat-CSS-Toolchain, `.claude/`-Agents + Skills. Junk (.bak/.legacy/agent-memory/alte docx) ausgeschlossen.
- **Server als 3. Quelle:** 237 Files inventarisiert, kein Hotfix-Verlust (alle Abweichungen < Session-Git), 19 `_preview`-Experimente = Noise. Deploy-Set 55 Runtime-Files berechnet.
- **Security:** RCON-PW + management-server-secret in `docs/server_snapshot/` redigiert (HEAD), `.secrets.baseline` regeneriert.
- VERSION → 4.4.0. Verifikation grün (5 Tests, py_compile, Jinja).
- Detail: `docs/KONSOLIDIERUNG_2026-06-01.md`.

---

## 1. Bot-Architektur (3-Bot-System)

| Bot | Datei | Funktion |
|-----|-------|----------|
| GameServer-Bot | `bots/gameserver_bot.py` (15 KB) | Satisfactory-Steuerung, Slash-Commands, Blueprints, Savegames |
| Monitor-Bot | `bots/monitor_bot.py` (95 KB) | Health-Monitoring, Dashboard, Auto-Tasks, Scheduler, MC-Integration |
| Admin-Bot | `bots/admin_bot.py` (8 KB) | Moderation, Leveling, Tickets, Reaction Roles, Giveaways |

---

## 2. Satisfactory-Integration (komplett)

| Modul | Datei | Status |
|-------|-------|--------|
| Server-Verwaltung | `modules/satisfactory/server.py` (12 KB) | systemd start/stop/restart, /proc-Fallback |
| HTTPS API-Client | `modules/satisfactory/api_client.py` (8 KB) | Satisfactory Dedicated Server API |
| Savegame-Analyse | `modules/satisfactory/savegame_analyzer.py` (34 KB) | Header-Parsing, Statistiken |
| Blueprint-Manager | `modules/satisfactory/blueprint_manager.py` (20 KB) | Upload, Download, ZIP-Verwaltung |
| Whitelist/Blacklist | `modules/satisfactory/whitelist.py` + `blacklist.py` | Spielerverwaltung |
| Settings-Backup | `modules/satisfactory/settings_backup.py` (6 KB) | Game-Settings sichern |
| SteamCMD-Updater | `modules/monitoring/update_checker.py` (15 KB) | Build-ID Check, Auto-Update mit HAR-Suppress |

---

## 3. Minecraft Multi-Server-Integration (komplett)

| Modul | Datei | Status |
|-------|-------|--------|
| Server-Verwaltung | `modules/minecraft/server.py` (18 KB) | Multi-Server (BMC5 NeoForge + Vanilla), systemd, RCON |
| RCON-Protokoll | `modules/minecraft/rcon.py` (9 KB) | Async Little-Endian Binary Protocol |
| Chat-Bridge | `modules/minecraft/chat_bridge.py` (17 KB) | Log-Polling → Discord, Discord → RCON, Vanilla + NeoForge |
| Backup-Manager | `modules/minecraft/backup.py` (14 KB) | World-Backup/Restore, Rotation (max 20) |
| Settings-Backup | `modules/minecraft/settings_backup.py` (9 KB) | server.properties Sicherung |
| Update-Checker | `modules/minecraft/update_checker.py` (14 KB) | Paper API v2 für Vanilla |
| Blacklist | `modules/minecraft/blacklist.py` (11 KB) | Cross-Server Bans, SQLite |
| Welt-Analyse | `modules/minecraft/world_analyzer.py` (14 KB) | level.dat, Spielerstatistiken |
| BMC5-Migration | — | Better MC 3 (Forge) → Better MC 5 (NeoForge 1.21.1) abgeschlossen |

---

## 4. Auto-Update-System Kern-Module (lokal fertig, nicht deployed)

| Modul | Datei | Größe | Status |
|-------|-------|-------|--------|
| UpdateManager | `modules/minecraft/update_manager.py` | 32 KB | Zentraler Orchestrator Phase 0-8, asyncio.Lock, Crash-Recovery |
| FileManager | `modules/minecraft/file_manager.py` | 24 KB | Streaming-Download, ZIP, SHA1/MD5, Atomic Swap, Disk-Check |
| MCCountdownTimer | `modules/minecraft/mc_countdown.py` | 11 KB | Erbt RestartTimer, RCON /title Banner, Farbkodierung |
| ModpackUpdater | `modules/minecraft/modpack_updater.py` | 15 KB | CurseForge API v1, Rate-Limiter, Server-Pack-Erkennung |
| NeoForgeUpdater | `modules/minecraft/neoforge_updater.py` | 10 KB | Versions-Erkennung (3 Quellen), Installer-Logik |
| DB-Migration v4 | `modules/database/migrations.py` | 19 KB | modpack_updates + server_versions Tabellen |
| Feature-Plan | `docs/FEATURE_PLAN_AUTO_UPDATE.md` | 48 KB | Spezifikation v1.4, 16 Abschnitte |

---

## 5. Monitoring & Automatisierung (19 Subsysteme, alle komplett)

| Nr | Subsystem | Datei | Beschreibung |
|----|-----------|-------|-------------|
| 1 | Health-Check | `health_check.py` (9 KB) | Server-Erreichbarkeit prüfen |
| 2 | Health-Auto-Restart | `health_checker.py` (29 KB) | 3-Failure-Threshold, 30min Cooldown, Suppress-Logik |
| 3 | Performance-Monitor | `performance.py` (7 KB) | CPU/RAM/Disk Schwellwerte |
| 4 | Player-Tracker | `player_tracker.py` (22 KB) | Join/Leave, Spielzeit, Wochenreport |
| 5 | IP-Tracker | `player_ip_tracker.py` (26 KB) | IP-Logging, Ban-Erkennung |
| 6 | Stats-Collector | `stats_collector.py` (13 KB) | 5-Min-Metriken, 30 Tage Ring-Buffer |
| 7 | Stats-Tracker | `stats_tracker.py` (13 KB) | Historische Statistiken |
| 8 | Status-Writer | `status_writer.py` (28 KB) | JSON-Status für Web-Dashboard |
| 9 | Crash-Replay | `crash_replay.py` (9 KB) | Log-Kontext bei Crashes |
| 10 | Graceful Degradation | `graceful_degradation.py` (7 KB) | Fallback bei Service-Ausfall |
| 11 | Login-Audit | `login_audit.py` (6 KB) | Login-Protokollierung |
| 12 | Auto-Cleanup | `auto_cleanup.py` (9 KB) | Veraltete Daten aufräumen |
| 13 | Optimizer | `optimizer.py` (10 KB) | System-Optimierung |
| 14 | Savegame-Protection | `savegame_protection.py` (13 KB) | Crash-Loop-Erkennung, Integritätsprüfung |
| 15 | Steam-Changelog | `steam_changelog.py` (4 KB) | Changelog-Monitoring |
| 16 | Service-Watchdog | `service_watchdog.py` (14 KB) | systemd-Service-Überwachung |
| 17 | Forecasting | `forecasting.py` (14 KB) | Vorhersage-Analytik |
| 18 | Selftest | `selftest.py` (15 KB) | Startup-Systemcheck |
| 19 | Web-Status | `web_status.py` (4 KB) | Statische Status-Seite |

---

## 6. Web-Dashboard (komplett)

| Feature | Dateien | Beschreibung |
|---------|---------|-------------|
| Dashboard-Hauptseite | `web/routes/dashboard.py` | Server-Übersicht, Live-Status |
| Server-Detail | `web/routes/server_detail.py` (39 KB) | Einzelserver-Ansicht |
| Discord OAuth2 + Login | `web/auth.py` (16 KB) | JWT, bcrypt-Fallback |
| Konfiguration | `web/routes/config_route.py` (19 KB) | Live-Config-Editor |
| Analytics | `web/routes/analytics_route.py` (28 KB) | Diagramme, Statistiken |
| Security | `web/routes/security_route.py` (18 KB) | Fail2ban, SSL, Bans |
| System-Übersicht | `web/routes/system_route.py` (18 KB) | Disk, Pakete, Services |
| SSE Live-Updates | `web/routes/sse_route.py` (14 KB) | Server-Sent Events |
| Health-API | `web/routes/health_route.py` (11 KB) | REST-Endpunkte |
| Admin-Bot-Steuerung | `web/routes/admin_bot_route.py` (22 KB) | Web-Interface für Admin-Bot |

---

## 7. Benachrichtigungen (komplett)

| Kanal | Datei | Beschreibung |
|-------|-------|-------------|
| Discord-Notifier | `discord_notifier.py` (12 KB) | Embed-basiert, 5 NotifyLevel |
| E-Mail-Notifier | `email_notifier.py` (9 KB) | SMTP, HTML-Formatierung |

---

## 8. Backup-System (komplett)

| Modul | Datei | Beschreibung |
|-------|-------|-------------|
| SAT Backup-Manager | `backup_manager.py` (18 KB) | Komprimierte Tarballs, Rotation |
| MC Backup-Manager | `minecraft/backup.py` (14 KB) | World-Backup pro Server |
| OneDrive-Sync | `onedrive_backup.py` (8 KB) | Cloud-Backup via rclone |
| Config-Backup | `config_backup.py` (13 KB) | Konfigurations-Sicherung |
| Integritätsprüfung | `integrity.py` (12 KB) | Backup-Verifizierung |

---

## 9. Datenbank (SQLite, komplett)

| Modul | Datei | Beschreibung |
|-------|-------|-------------|
| DB-Manager | `db_manager.py` (6 KB) | Shared Connection, WAL-Modus |
| Migrationen | `migrations.py` (19 KB) | v1-v4, 23+ Tabellen, FTS5 |
| Models | `models.py` (17 KB) | Dataclass-Modelle |
| JSON-Importer | `json_importer.py` (28 KB) | Legacy JSON → SQLite |
| Maintenance | `maintenance.py` (16 KB) | Cleanup, Optimierung |
| Such-Index | `search_indexer.py` (14 KB) | FTS5 Volltextsuche |

---

## 10. Sicherheit (komplett)

| Modul | Datei | Beschreibung |
|-------|-------|-------------|
| Fail2ban | `fail2ban.py` (11 KB) | DDoS-Schutz Integration |
| Ban-Manager | `ban_manager.py` (12 KB) | Ban-Verwaltung + Expiry |
| SSL-Monitor | `ssl_monitor.py` (9 KB) | Zertifikats-Überwachung |
| Anti-Spam | `anti_spam.py` (8 KB) | Rate-Limiting |
| Wort-Filter | `word_filter.py` (7 KB) | Inhaltszensur |

---

## 11. Community-Features (Admin-Bot, komplett)

| Feature | Cog | Beschreibung |
|---------|-----|-------------|
| Moderation | `moderation_cog.py` (17 KB) | Mute, Warn, Kick, Ban |
| Warnsystem | `warn_cog.py` (26 KB) | Verwarnungen + Eskalation |
| Leveling | `leveling_cog.py` (21 KB) | XP-System, Ränge |
| Tickets | `tickets_cog.py` (28 KB) | Support-Ticket-System |
| Reaction Roles | `reaction_roles_cog.py` (28 KB) | Rollen per Reaktion |
| Giveaways | `giveaway_cog.py` (36 KB) | Verlosungen |
| Temp Voice | `temp_voice_cog.py` (23 KB) | Temporäre Sprachkanäle |
| TeamSpeak | `teamspeak_cog.py` (33 KB) | TS3-Integration + Bridge |
| Welcome | `welcome_cog.py` (25 KB) | Begrüßungs-System |
| Custom Commands | `custom_commands_cog.py` (26 KB) | Eigene Befehle erstellen |
| Profil | `profile_cog.py` (18 KB) | Benutzerprofile |
| Audit-Log | `audit_cog.py` (10 KB) | Protokollierung |

---

## 12. Netzwerk-Monitoring (komplett)

| Modul | Datei | Beschreibung |
|-------|-------|-------------|
| DuckDNS | `duckdns_monitor.py` (15 KB) | DNS-Verfügbarkeit |
| Port-Monitor | `port_monitor.py` (15 KB) | Port-Erreichbarkeit |
| Disk-Guard | `disk_guard.py` (16 KB) | Festplatten-Überwachung |
| Paket-Checker | `package_checker.py` (11 KB) | System-Update-Check |

---

## 13. Bugfixes (15. März 2026)

| Datei | Fix | Beschreibung |
|-------|-----|-------------|
| `status_writer.py` | float('inf') | bot.latency Infinity-Check |
| `gameserver_bot.py` | float('inf') | Gleicher Fix — wahrscheinlich Crash-Ursache für Offline |
| `admin_bot.py` | float('inf') | Gleicher Fix |
| `web/auth.py` | Memory-Leak | Rate-Limit-Dict Cleanup bei >1000 IPs |
| `update_checker.py` | Timeout | _safe_start() mit 90s asyncio.wait_for |
| `db_manager.py` | Timeout | WAL-Checkpoint mit 10s Timeout |
| `backup_manager.py` | Security | Path-Traversal-Schutz (is_relative_to) |

---

## 14. Infrastruktur

| Komponente | Details |
|------------|---------|
| 159 Python-Dateien | ~2.5 MB Gesamtcode |
| 23 Cogs | Slash-Commands für alle Funktionen |
| 40+ Web-Routes | FastAPI Dashboard |
| 23+ DB-Tabellen | SQLite mit FTS5 |
| 3 systemd-Services | monitor-bot, gameserver-bot, admin-bot |
| Web-Dashboard | Port 8080 intern, 443 extern via Nginx |
| Deployment | SCP + systemctl restart |

---

## Versions-Historie

| Version | Datum | Highlights |
|---------|-------|------------|
| v1.0.0 | Feb 2026 | Initiales System |
| v2.2.0 | Feb 2026 | 12 kritische Fixes, Security |
| v3.0.0 | Feb 2026 | Minecraft Multi-Server |
| v3.1.0 | Feb 2026 | MC Autosave, Blacklist, Modpack-Check |
| v3.2.0 | Feb 2026 | Web-Dashboard + Command Cleanup |
| v4.0.0 | 22. Feb 2026 | 39 Features (F27-F65), Dashboard, Security, DB |
| v4.0.1 | 12. Mär 2026 | BMC5-Migration (NeoForge 1.21.1) |
