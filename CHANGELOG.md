# Changelog

Alle relevanten Aenderungen am Discord Bot System werden hier dokumentiert.

---

## [Unreleased] — Community-Rebuild (Branch-Linie, teils live seit 2026-06-04)

> Multi-Tenant-Umbau + Community-Features (MVP-first). MVP + Wave-2 seit
> 2026-06-04 auf Prod (Migration Schema 4→7 datensicher). Version-Bump offen.

### Multi-Tenant-Fundament
- **Neu `modules/guild_context.py`:** Guild-Resolver (`get_primary_guild_id`/`get_active_guild_ids`) + `GuildConfig` (schema-getriebene Per-Guild-Settings, Feature-Flags, Guild-Registry, JSON-Roundtrip, 15s-TTL-Cache gegen Cross-Prozess-Drift). Loest verstreute `GUILD_ID`-Single-Guild-Annahme ab.
- **Migration v5:** Tabellen `guilds`/`guild_config`/`guild_modules`. Daten-Isolation pro Guild (kein Cross-Guild-Leak) — test-bewiesen.
- **Guild-Registry-Auto-Befuellung:** monitor-bot `on_ready` upsertet alle `bot.guilds` idempotent.

### Leveling-Rebuild (Arcane-Tier, per-Guild)
- **Migration v6:** `leveling` guild-scoped (`UNIQUE(guild_id,user_id)`, recreate-copy-drop, Daten erhalten, Backfill) + neue `voice_sessions`-Tabelle.
- **Echte Voice-Sessions statt 5-Min-Ticks** (`modules/voice_sessions.py`): Anti-Cheat — XP nur bei ≥2 Menschen, kein self/server-deaf, nicht AFK; akkumuliert nur valide Sekunden.
- **No-XP-Channels, Leaderboard-Pagination + eigene Position, Role-Rewards** (Sammel- vs Aufstiegs-Modus, `/levelrewards`), Rank-Card als 1-Admin-Template (BG/Akzent per-Guild).
- Config JSON → `guild_config` (Dashboard-editierbar), sync Hot-Path-Cache.

### Dashboard
- **Config-Bridge** (`web/guild_config_bridge.py`): Leveling-Dashboard ↔ per-Guild `guild_config` (Format-Konvertierung, Cross-Prozess via TTL).
- **Web-Leaderboard** (`web/routes/leveling_route.py` + `leveling.html`): `GET /leveling`, Top-100 guild-scoped, require_auth (noch nicht deployed).

### Linked-Accounts (Wave 2)
- **Migration v7:** `user_linked_accounts` (`UNIQUE(guild_id,user_id,platform)`). `modules/linked_accounts.py` + `/link`·`/unlink`·`/accounts` (Plattform-Whitelist, Cooldowns, escape+AllowedMentions.none).

### Security (Phase G Core)
- **Verification-Gate:** persistenter Verify-Button-Role (`cogs/verification_cog.py`), `/sicherheit`-Gruppe.
- **Raid-Detection** (`modules/raid_detector.py`): Join-Spike-Gleitfenster pro Guild + Alarm-Cooldown.

### Sonstiges
- **`/setup_topics`** (`cogs/channel_setup_cog.py`): Channel-Topics automatisch setzen (dry-run-Default).
- **Embed-Helper** (`utils/embeds.py`) als Stil-Fundament.
- **Tech-Debt M2:** tote `UpdateChecker`-Instanz aus gameserver-bot entfernt (SAT-Update-Check laeuft ausschliesslich im monitor-bot).
- **Ping-Haertung:** `everyone=False`-Guard fuer gewollte Pings (Ticket-Support-Notify, Giveaway-Gewinner).
- **Tests:** `test_guild_context` (32), `test_leveling` (59), `test_voice_sessions` (24), `test_linked_accounts` (18), `test_raid_detector` (18), `test_migration_v7` (6), `test_channel_topics` (11), `test_dashboard_bridge` (18) u.a.

---

## [4.4.0] — 2026-06-02

### Konsolidierung master ↔ main (+ Server-Quelle)

- **3-Wege-Merge** der parallelen Entwicklungslinien (`main` = V5/Updater/manual_stop ↔ `master` = ~40 weiterentwickelte Module) via gemeinsamem Vorfahr `9c680a5` — keine Funktion verloren, orthogonale Features kombiniert statt eine Seite verworfen.
- **`db_manager`:** Read-Pool (main) **und** Cross-Prozess-Write-Retry (master) beide aktiv.
- **`reaction_roles_cog`:** GC-sicheres `track_task` (main) **und** Panel-Lock-Cleanup-Loop-Start (master).
- **`pipeline_approval_cog`:** master-Superset (Approve/Dismiss/**Recat**) übernommen.
- **Monitoring (`health_checker`/`service_watchdog`/`package_checker`):** main-`manual_stop`/0-Update-Filter behalten, master-Änderungen auto-gemerged.
- **master-Module dazu:** `scripts/rcon_op.py`, `web/tools/build_css.py`, 6 Server-Test-Files, Basecoat-CSS-Toolchain, `.claude/`-Agents + Skills.
- **Frontend:** main-V5 autoritativ (master-Alt-Design verworfen); verwaiste Endpoints nachverdrahtet (Dashboard **Events-Clear-Button**). Mods-Daten in V5 im Updates-Tab integriert.
- **Server als 3. Vergleichsquelle** inventarisiert (237 Files, kein Hotfix-Verlust; Redeploy-Liste erstellt). Detail: `docs/KONSOLIDIERUNG_2026-06-01.md`.
- Junk bereinigt: `.bak`/`.legacy`/alte `.docx`/`agent-memory` nicht übernommen.

---

## [4.3.0] — 2026-06-01

### Dashboard — V5 Midnight-Navy Redesign

- **Neues Shell-Template `web/templates/base_v5.html`** (appside/appmain-Layout, Midnight-Navy Data-Terminal-Look) + zentrales `web/static/_preview/v5_components.css` mit Compat-Layer der alten `style.css`-CSS-Variablen → bestehendes Markup laeuft ohne Cross-File-Edits weiter.
- **`dashboard.html` komplett neu:** Bento-Hero-Grid, Mono-Zahlen, fixe Chart-Hoehe (`.chart-wrap` 220px gegen Endlos-Wachstum), blaue Chart.js-Palette (#3b82f6/#60a5fa/#6d5cf6/#22d3ee). Alle SSE/HTMX-Hooks unveraendert.
- **8 weitere Seiten auf `base_v5.html` umgestellt:** errors, system, security, admin_bot, config, server_detail, search, changelog. Tabs einzeilig scrollbar (nowrap + overflow-x), `.appcont` max-width 1120 → 1320px.
- **`base.html`:** Theme-Toggle entfernt (Dark fix), `style.css` auf Midnight-Navy rethemed.
- Geist-Font + kompiliertes Tailwind/Basecoat-Output + Navy-Tokens (`tailwind/_tokens.css`).

### Server-Control — Manuelles Stoppen wird respektiert

- **Neu `modules/monitoring/manual_stop_state.py`:** persistenter State (`data/manual_stop_state.json`, atomic Read-Modify-Write unter Lock), Service↔server_id-Mapping. Dashboard-Stop/Maintenance setzt Flag, Start/Restart loescht ihn.
- **4 Auto-Restart/Warning-Quellen respektieren den Flag:** `health_checker` (keine Failure-Warnings/Down-Notify/Auto-Restart), `service_watchdog`, `scheduler_cog` (Daily-Restart SAT+MC), `port_monitor` (keine Port-Down-Warnings).
- **Self-Healing:** wird ein manuell gestoppter Server out-of-band (SSH/Webmin) wieder gestartet, erkennt `health_checker` die Erreichbarkeit, loescht den Flag automatisch und reaktiviert die Ueberwachung — kein Monitoring-Blindspot.

### System-Updates — Updater-Ueberarbeitung

- **Parser auf `apt-get -s upgrade`** statt `apt list --upgradable` (versteckte Phased-Updates → fehlerhafte Zaehlung). LANG=C erzwingt parsbaren Output (vorher Locale-Bug: deutscher apt-Output → leere Liste).
- **Held-back-Pakete sichtbar:** `<details>`-Sektion (Diff dist-upgrade − upgrade) mit eigenem **Full-Upgrade-Button** (`apt full-upgrade -y`, Warn-Confirm da Pakete entfernt werden koennen).
- **Root-Wrapper-Scripts** `/usr/local/sbin/dashboard-apt-upgrade` + `-fullupgrade` (DEBIAN_FRONTEND=noninteractive + force-confold/confdef) — fixt debconf-Dialog-Fehler durch `sudo` env_reset. Output gefiltert (`_clean_apt_output`) + `<pre>`-formatiert statt rohem char-wrap.
- **Reboot-Required-Banner** (`/var/run/reboot-required`) in Check/List/Upgrade-Response. **HX-Trigger** `packageListChanged` refresht Liste nach Upgrade automatisch.
- **Events-Feed:** `0 Updates verfuegbar`-Cron-Noise gefiltert (source-side in `package_checker._persist_to_db` + query-side in `dashboard.py`/`sse_route.py`).

### Leveling — Bild-Hintergrund fuer Level-Up

- **Neu `modules/levelup_card.py`:** Pillow-gerenderte Level-Up-Card (900×280, hochgeladenes Bild als Hintergrund + Avatar, Level, XP-Progress-Bar). Config-Methoden in `leveling.py`, `/xp levelcard`-Command, Guarded-Import mit Embed-Fallback wenn Pillow fehlt. `Pillow` in requirements.txt.

### Sonstiges

- Webmin-Zugang: ufw + miniserv-allow auf aktuelle Marco-IP angepasst.
- `/review` Multi-Agent-Audit der Session-Diff: 0 CRITICAL/HIGH, F01-F03 (Self-Heal, Tests, Race-Fix) gefixt.
- Tests: `tests/test_manual_stop_state.py` (8 Tests).
- Repo auf GitHub gepusht (`Marco-Satis/discord-bots`, main).

---

## [4.2.0] — 2026-05-17

### Audit-Fixes (Full-System-Review Master-Report)

- **Fix-1 CRASH:** `web/routes/errors_route.py:14` — `RedirectResponse` Import ergaenzt (Worktree + Server). Vorher latenter `NameError` beim Aufruf von `/api/errors/clear`.
- **Fix-2 HIGH (DSGVO):** Fire-and-Forget Tasks gegen GC-Verlust gesichert in 9 Modulen. Neue Utility `utils/async_tasks.py` mit `track_task()` + `schedule_from_sync()` + `BackgroundTaskMixin`. Betroffen: `player_ip_tracker`, `stats_tracker`, `player_tracker`, `giveaways`, `leveling`, `monitor_bot`, `general_cog`, `reaction_roles_cog`.
- **Fix-3 HIGH CROSS:** SSE-Hot-Path entkernt — `web/routes/sse_route.py` von 2026-05-16 sse-starlette-Refactor in Worktree gepullt (`interval=None`, `asyncio.to_thread` fuer alle 3 Collector, `EventSourceResponse` statt manuellem text/event-stream).
- **Fix-5 HIGH:** `modules/database/db_manager.py` Read-Pool (2 Read-Connections, Round-Robin via `get_read_db()`) + `web/routes/analytics_route.py` `analytics_peaks` mit WHERE-Window (90d) + LIMIT 5000 statt Full-Table-Scan.
- **Fix-7 HIGH:** `modules/minecraft/backup.py` World-Backups jetzt atomar — Copy in `.tmp`-Verzeichnis, danach `os.rename` zum finalen Pfad. `list_backups` + `_cleanup_old_backups` filtern `.tmp` aus. Verhindert halbe Backups durch Crash mid-copy.
- **Fix-9 MEDIUM (partial):** `utils/channel.py` Helper-Modul (`get_text_channel`, `get_voice_channel`, `get_messageable_channel`) mit `isinstance`-Guards + Diagnose-Logging fuer typsichere Channel-Lookups. Migration der 62 Call-Sites in 15 Files steht als Backlog.

### Vorbereitet, manuelle Aktion noetig

- **Fix-4 HIGH:** `satisfactory-limits.conf` Drop-In hochgeladen nach `/tmp/satisfactory-limits.conf`. Manuell installieren: `sudo mkdir -p /etc/systemd/system/satisfactory.service.d && sudo cp /tmp/satisfactory-limits.conf /etc/systemd/system/satisfactory.service.d/limits.conf && sudo systemctl daemon-reload && sudo systemctl restart satisfactory`.

### Audit-Befunde aus Full-System-Review (2026-05-17)

- 10 Sub-Agent-Reports unter `~/.claude/audit/FULL_REVIEW_2026-05-17/` (security, async, deps, perf, infra, quality, docs, minecraft, satisfactory, web_research) + MASTER_REPORT.md mit Top-10-Action-Plan
- 0 CRITICAL, 9 HIGH, 18 MEDIUM Findings ueber 8 Achsen (Discord-Bots, FastAPI, Modules, Server, DB, Deps, Hygiene, Gameserver)
- 0 Bandit HIGH/MEDIUM, 0 Secrets im Repo (detect-secrets clean), 0 pip-audit-CVEs in 38 Python-Paketen
- Cross-Report-Konfirmationen: SSE-Block (async+perf), satisfactory-Limits (infra+sat)

### Backlog (im Master-Report dokumentiert)

- Fix-6 BMC Mod-Hash-Lockfile (288 Mods ohne Supply-Chain-Verify)
- Fix-8 Worktree-vs-Server Drift Reconcile (3 verbleibende Files: base.html, app.py, monitor_bot.py — requirements.txt + sse_route.py + errors_route.py bereits konvergiert)
- Fix-9 get_channel-Migration der 62 Call-Sites
- Fix-10 Starlette+FastAPI Upgrade (Starlette CVE-2025-62727 / -54121, transitiv)
- Cleanup: DB chmod 640, nginx Rate-Limit + TLS-Hardening, vanilla `management-server-secret` rotieren, `docs/production/` 10 Guides ergaenzen, 4 fehlende `.claude/rules/`-Files commit

---

## [4.1.0] — 2026-03-15

### Hinzugefuegt

- **Auto-Update-System (A0 + I1-I9 + B0-B4 + C)**
  - UpdateManager: Vollstaendiges Modpack-Update mit Atomic Swap + Rollback
  - ModpackUpdater: CurseForge API Integration (Versions-Check + Download)
  - FileManager: Streaming-Download mit SHA1/MD5 Hash-Verifikation
  - NeoForgeUpdater: Automatische NeoForge-Installation nach Modpack-Update
  - MCCountdownTimer: In-Game Countdown mit RCON-Warnungen
  - UpdateChecker: SteamCMD Build-ID Check fuer Satisfactory
  - Chat-Bridge: 8 In-Game-Befehle (!status, !version, !players, etc.)
  - Discord-Commands: /mc modpack status/update/cancel/rollback/history/check
  - Discord-Commands: /sat update + /sat update cancel
  - Scheduler: Modpack-Check 12:00+00:00, Auto-Update 04:00
  - DB Schema v4: modpack_updates + server_versions Tabellen
  - ENV-Dokumentation: config/.env.example aktualisiert

### Gefixt

- **SAT CPU/RAM zeigt 0**: _find_process() gab Wrapper-Prozess statt Game-Prozess zurueck.
  Fix: Waehlt jetzt den Prozess mit dem meisten RAM (FactoryServer-Linux-Shipping).
- **CSRF-Schutz deaktiviert**: Middleware pruefte session.user (immer None) statt JWT-Cookie.
  Fix: Prueft jetzt dashboard_token Cookie-Existenz.
- **BUG-1**: mc_countdown.py Race Condition — asyncio.create_task statt call_later
- **BUG-2**: file_manager.py doppelte Hash-Berechnung — Streaming-Hash wiederverwendet
- **BUG-3**: neoforge_updater.py RAM-Ueberlauf — iter_chunked statt resp.read()
- **BUG-4**: update_manager.py Update auf laufendem Server — Abbruch bei Stop-Timeout
- **BUG-5**: update_manager.py kein RCON stop — RCON zuerst, systemctl als Fallback
- **BUG-6**: update_manager.py NeoForge im falschen Ordner — nach Atomic Swap installiert
- **BUG-7**: update_manager.py kein Rollback bei Crash — Rollback nach 3 fehlgeschlagenen Starts
- **RISK-5**: update_manager.py HAR-Suppress zu kurz — bei jedem Phasenwechsel +900s

### Aufgeraeumt

- 171 __pycache__ Verzeichnisse auf Server bereinigt
- Unbekannte Ports dokumentiert (8081=Nginx, 8888=SAT RCON, 9090=Webmin)

---

## [4.0.0] — 2026-02-22

### Hinzugefuegt

- **Phase 1: Sicherheit + Stabilitaet (14 Features)**
  - F62: Selftest — Pre-Boot-Pruefungen (ENV, Config, Permissions, Packages)
  - F61: Shutdown — Graceful Shutdown mit Signal-Handler + Cleanup-Callbacks
  - F64: CSRF-Schutz — Token-basierte Middleware fuer POST/PUT/DELETE/PATCH
  - F65: Session Timeout — Automatische Abmeldung nach 60 Min Inaktivitaet
  - F27: Health Auto-Restart — SAT+MC UDP/TCP-Probes, Auto-Restart bei haengendem Server
  - F49: Disk Guard — 3-Stufen Disk-Warnung (Warning/Critical/Emergency)
  - F50: Service Watchdog — systemd-Service-Monitoring mit Auto-Restart
  - F51: DuckDNS Monitor — DNS-vs-IP-Pruefung mit Auto-Update
  - F52: Port Monitor — TCP-Connect-Tests fuer alle kritischen Ports
  - F31: Fail2Ban-Monitoring — Echtzeit-Status, Ban-Statistiken, Dashboard-Widget
  - F32: SSL-Zertifikat-Monitor — Ablauf-Pruefung, automatische Warnungen
  - F33: Backup-Integritaet — SHA256-Checksummen, tar-Validierung, Groessen-Check
  - F34: Health-Route — /api/health Endpoint mit Server/Bot/System-Status
  - F48: Rate-Limiter — Token-Bucket pro IP (Login 5/min, Actions 10/min, Read 60/min)

- **Phase 2: Datenbankschicht (4 Features)**
  - F28: SQLite-Migration — aiosqlite WAL-Modus, 31 Tabellen, JSON→SQLite Dual-Read
  - F63: Retention/Cleanup — Automatisches Bereinigen alter Datenbankeintraege
  - F56: Backup-Rotation — Automatische Rotation von DB- und Server-Backups
  - F53: Config-Versionierung — Aenderungs-Historie der config.json

- **Phase 3: Dashboard-Erweiterungen (9 Features)**
  - F29: SSE Live-Updates — Server-Sent Events fuer Echtzeit-Dashboard
  - F35: Korrelations-Dashboard — CPU/RAM/Tick-Rate Zusammenhaenge visualisieren
  - F36: Export-Funktionen — CSV/JSON-Export von Monitoring-Daten
  - F37: Ressourcen-Forecasting — Lineare Regression fuer Disk/RAM-Prognosen
  - F44: Error-Dashboard — Fehler-Uebersicht mit Filterung und Gruppierung
  - F55: Dashboard-Suche — FTS5-Volltextsuche ueber alle Dashboard-Inhalte
  - F57: Stats-Collector — Zentrale Metrik-Erfassung (CPU, RAM, Spieler, Tick-Rate)
  - F58: Analytics-Dashboard — Heatmaps, Peaks, Trends, Server-Vergleich
  - F45: Changelog-Seite — Steam/Satisfactory Update-Historie im Dashboard

- **Phase 4: Bot-Erweiterungen (7 Features)**
  - F30: Crash-Replay — Automatische Log-Analyse nach Server-Crashes
  - F39: Moderation — Warn-System, Auto-Mod, Timeout, Ban mit Audit-Trail
  - F40: Leveling — XP-System mit Rollen-Rewards und Leaderboard
  - F41: Giveaways — Gewinnspiel-System mit Timer und Teilnehmer-Verwaltung
  - F43: Custom Commands — Benutzerdefinierte Bot-Befehle per Discord
  - F54: Alert-Deduplizierung — Identische Warnungen zusammenfassen statt spammen
  - F59: Graceful Degradation — Feature-Isolation bei Teilausfaellen

- **Phase 5: Polishing (5 Features)**
  - F38: Maintenance-Mode — Bot-weiter Wartungsmodus mit Benutzer-Info
  - F42: Paket-Checker — apt-Update-Pruefung mit Dashboard-Anzeige
  - F46: Dark Mode — CSS-Theme-Toggle fuer das Dashboard
  - F60: Webhook-Integration — Externe Systeme per Webhook anbinden
  - F47: Performance-Optimierung — Connection-Pooling, Caching, Index-Optimierung

### Behoben (Review-Fixes)

- Middleware-Reihenfolge in web/app.py korrigiert (SessionMiddleware als aeusserste Schicht)
- Health-Checker Schwellenwerte angepasst (Failures 3→10, Timeout 10→20s)
- DB-Backup Thread-Safety in maintenance.py (eigene Connections in asyncio.to_thread)
- FTS5 Search-Index initial befuellt (12 Eintraege)
- DB-Backup nach Thread-Safety-Fix verifiziert (0.48 MB mit Integritaet OK)

### Technisch

- 39 Features in 5 Phasen implementiert
- Reine Python-Implementierungen (keine numpy/scipy Abhaengigkeit) fuer Forecasting + Korrelation
- SQLite WAL-Modus mit aiosqlite fuer concurrent access, 31 Tabellen inkl. FTS5
- SSE ersetzt HTMX-Polling fuer Echtzeit-Updates
- Middleware-Stack (LIFO): CORSMiddleware → RateLimitMiddleware → CSRFMiddleware → SessionTimeoutMiddleware → SessionMiddleware (aeusserste)
- 20 API-Router im Web-Dashboard registriert
- 26 Cogs in 3 Bots geladen

---

## [3.2.0] — 2026-02-20

### Hinzugefuegt

- **Phase 13: Web-Dashboard (F13)**
  - Vollstaendiges Admin-Dashboard mit FastAPI + HTMX + Jinja2
  - Discord OAuth2 Login mit Guild-/Rollen-Pruefung + Passwort-Fallback (bcrypt)
  - Dashboard-Uebersicht: Server-Kacheln, System-Performance, Bot-Status, Event-Feed
  - Server-Detail: Spielerliste, RCON-Konsole, Backups, Savegame-Info, Mods, Config, Analyse
  - Fehler-Uebersicht: Letzte ERROR/WARNING aus allen Bot-Logs
  - Admin Bot Setup: 10 Konfigurations-Tabs (Temp Voice, TS, WordFilter, AntiSpam, Warns, Reaction Roles, Leveling, Tickets, Audit, Giveaways)
  - Config-Panel: Feature-Toggles, Benachrichtigungs-Routing-Matrix, Login-Verwaltung, Bot-Profile
  - System-Seite: Echtzeit-Systeminfo (CPU, RAM, Disk) + Webmin-iframe-Einbettung
  - Stats Collector: Hintergrund-Task fuer Performance-Daten (5-Min-Intervall, Ringbuffer)
  - Analyse-API: REST-Endpunkte fuer Uptime, Performance, Spieler-Aktivitaet, Backup-Stats
  - 11 Python-Module (web/), 26 HTML-Templates, Dark-Theme

- **Phase 14: Command-Aufraeumung (F25)**
  - Server-Steuerung (start/stop/restart/cancel) aus Discord entfernt → Dashboard
  - Admin-Config-Commands (set, update, autosave, settings_backup/restore) entfernt → Dashboard
  - `/sat backup` umbenannt zu `/sat sav` (Savegame-Verwaltung)
  - `/server` und `/ping` entfernt (Dashboard zeigt alles)
  - Mod-Admin-Commands (install/uninstall/update/search/export/import) entfernt → Dashboard
  - Maintenance-Commands komplett ins Dashboard migriert
  - Hilfe-Uebersicht aktualisiert (rollenbasiert, nur verbleibende Commands)
  - ~2100 Zeilen Code entfernt, nur Lese-Commands bleiben in Discord

- **Phase 10-12: Diverse Verbesserungen**
  - MC IP-Ban wie SAT (UFW-Firewall, F23)
  - MC Ankuendigungs-Banner /mc say (F21)
  - MC Gameplay-Commands entfernt (F22: difficulty/weather/time/gamemode → nur In-Game)
  - SAT Auto-Update Verbesserung (F20: sofort bei leerem Server)

### Sicherheit

- XSS-Schutz: html.escape() fuer alle User-Inputs in HTMLResponse
- CSRF-Schutz: SameSite=lax Cookies + OAuth2 State-Token (session.pop)
- Exception-Leak-Prevention: Interne Fehlermeldungen nur im Server-Log
- Rate-Limiting: Max 5 Login-Versuche pro 15 Minuten
- JWT-Session: httpOnly Cookies, 24h Ablaufzeit
- Unused-Import-Cleanup in allen Web-Modulen

### Geaendert

- Satisfactory: backup_grp → sav_grp, config_load → sav_load, config_stats → sav_stats
- Minecraft: config_grp Beschreibung auf "nur Lesen" gesetzt
- Mod-Cog: Nur noch list + info Commands (Spieler-sichtbar)
- Maintenance-Cog: Leere Huelle (alle Commands ins Dashboard)
- .env.example: Neue Dashboard-Variablen (WEB_*, DISCORD_CLIENT_*, WEB_WEBMIN_URL)

---

## [3.1.0] — 2026-02-20

### Hinzugefuegt

- **Phase 8a: Server-Offline-Decorator**
  - `@server_online_required(server_attr)` Decorator fuer SAT-Commands
  - `_require_online_server()` Helper fuer MC Multi-Server-Commands
  - Wiederholendes `if not await srv.is_running(): return` Pattern refactored

- **Phase 8b: MC Autosave-Command**
  - `/mc config autosave <intervall> [server]` — Periodisches save-all via RCON
  - JSON-Persistenz in `data/mc_autosave.json`
  - cog_load/cog_unload Lifecycle fuer Task-Management

- **Phase 8c: Backup-Statistiken**
  - `/backup stats` — Detaillierte Backup-Statistiken pro Server
  - Disk-Usage mit Farbcodierung und Fortschrittsbalken
  - Async I/O via run_in_executor

- **Phase 8d: Config-Backup Rotation + Verschluesselung**
  - Konfigurierbare max_backups aus config.json
  - Optionale GPG-Verschluesselung (AES256, symmetrisch)
  - ENV: `GPG_PASSPHRASE`, config: `backup.config_encrypt`

- **Phase 8e: MC Blacklist-System**
  - Serveruebergreifendes Ban-System fuer Minecraft
  - `/mc blacklist add|remove|list|history`
  - Ban-Historie mit active/inactive Status
  - Automatische Blacklist-Integration bei `/mc players ban`
  - Mention-Injection-Schutz in allen Embeds

- **Phase 8f: Scheduled Messages**
  - `/schedule add|list|cancel` — Geplante Nachrichten
  - Relative/absolute Zeitangaben, Wiederholung (einmalig/taeglich/woechentlich)
  - Persistenz in `data/scheduled_messages.json`
  - Zeitzone: Europe/Berlin, max. 20 aktive Schedules

- **Phase 8g: Web-Status-Seite**
  - HTML-Generator mit Jinja2-Template (Dark-Mode, responsive)
  - Auto-Refresh alle 60s, Farbcodierung, System-Performance
  - Nginx-Setup-Script (`scripts/setup_nginx.sh`)
  - ENV: `WEB_STATUS_ENABLED`, `WEB_STATUS_PATH`

- **Phase 8h: BMC Modpack-Update-Check**
  - Modrinth/CurseForge API Unterstuetzung
  - Periodischer Check alle 12h mit Admin-Benachrichtigung
  - `/mc config modpack_check` fuer manuellen Check
  - ENV: `MC_BMC_MODPACK_ID`, `MC_BMC_MODPACK_VERSION`, `MC_BMC_MODPACK_SOURCE`

- **/clear Abbruchfunktion**
  - `/clear` ohne Parameter bricht laufenden Loeschvorgang ab
  - Cancel-Event-System fuer sauberen Abbruch

### Behoben (Phase 7 + Phase 9 Re-Review)

- 20 CRITICAL + 8 WARNING Fixes aus Komplett-Review (63 Dateien)
- Scheduled Messages: changed-Flag korrekt initialisiert
- Scheduled Messages: Nachrichtenlaenge auf 2000 Zeichen validiert

### Sicherheit

- Mention-Injection-Schutz in allen neuen Embeds (discord.utils.escape_mentions)
- RCON-Input-Sanitisierung fuer alle neuen Commands
- GPG-Verschluesselung fuer Config-Backups (optional)

---

## [3.0.0] — 2026-02-20

### Hinzugefuegt

- **Minecraft Multi-Server Integration (Phase 14a-14o)**
  - Unterstuetzung fuer 2 MC-Server: Better MC (BMC3 Fabric) + Vanilla/Paper
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
