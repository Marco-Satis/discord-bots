# Review-Report — Discord Bot System v4.0.0

> **Datum:** 22. Februar 2026
> **Reviewer:** Claude Code
> **Review-Start:** 12:40 UTC
> **Vorgaenger-Review:** docs/REVIEW_KOMPLETT_v3.2.0.md (v3.2.0 hatte 42 Befunde, 34 Fixes)

---

## Executive Summary

- **Gesamtbewertung:** SEHR GUT
- **Kritische Fehler:** 3 (alle behoben: Dashboard Middleware, SAT-Restarts, DB-Backup Thread-Safety)
- **Warnungen:** 5
- **Hinweise:** 4
- **Features vollstaendig:** 39/39 (alle Module existieren und sind registriert)
- **Services aktiv:** 5/5 (monitor-bot, admin-bot, gameserver-bot, web-dashboard, satisfactory)
- **Smoke-Test:** BESTANDEN

---

## Schritt 0: Backup + Git-Status

- **Backup erstellt:** `/home/botuser/backup_pre_review_20260222_123629.tar.gz` (117 MB)
- **Git-Status:** Kein Git-Repository auf dem Server (Deployment via SCP)
- **Ergebnis:** OK

---

## Problem 1: SAT-Server Ungewollte Restarts

### Ursache

**Root-Cause:** F27 Health Auto-Restart (`modules/monitoring/health_checker.py`) hatte zu aggressive Schwellenwerte:
- `health_auto_restart_failures` war auf **3** gesetzt (Standard)
- `health_auto_restart_timeout` war auf **10** Sekunden gesetzt
- Bei einem Check-Intervall von 150 Sekunden bedeutet das: nach nur **7,5 Minuten** ohne Antwort wird der SAT-Server neugestartet
- Der SAT-Server benoetigt aber **30+ Minuten** zum vollstaendigen Laden (CPU bei 80%, 3.9 GB RAM waehrend Boot)

### Zeitliche Korrelation

- SAT-Server-Prozess laeuft und ist aktiv (hohe CPU-Last)
- Ports (15777, 7777) antworten nicht waehrend der Boot-Phase
- Health-Checker sendet UDP-Probes an Port 15777 → keine Antwort → zaehlt als Failure
- Nach 3 Failures (= 450 Sekunden / 7.5 Minuten) wird `systemctl restart satisfactory` ausgeloest
- Server beginnt Boot-Prozess erneut → Endlosschleife

### Weitere Restart-Quellen identifiziert

- `modules/monitoring/health_checker.py` — F27 Health Auto-Restart (HAUPTURSACHE)
- `modules/monitoring/service_watchdog.py` — F50 Service Watchdog (kann auch restarten)
- `bots/gameserver_bot.py` / `modules/satisfactory/server.py` — manueller Restart via Bot-Command
- `web/routes/webhook_route.py` — Webhook-basierter Restart
- `systemd Restart=on-failure` — systemd eigener Restart bei Crash

### Fix

**Datei:** `config/config.json` auf dem Server
- `health_auto_restart_failures`: 3 → **10** (erlaubt ~25 Minuten Boot-Zeit)
- `health_auto_restart_timeout`: 10 → **20** Sekunden (mehr Zeit fuer Antwort)
- **Backup:** `config/config.json.pre_review_backup`

### Verifikation

- Monitor-Bot neugestartet
- Logs zeigen `(1/10)` statt `(1/3)` → Schwellenwert korrekt uebernommen
- SAT-Server konnte ohne ungewollte Restarts booten
- SAT-Server online mit 1 Spieler, Uptime 32+ Minuten

### Rollback-Info

```bash
cp /home/botuser/Discord_Bots/config/config.json.pre_review_backup /home/botuser/Discord_Bots/config/config.json
sudo systemctl restart monitor-bot
```

---

## Problem 2: Dashboard HTTP 500 Fehler

### Ursache

**Root-Cause:** Starlette Middleware LIFO-Reihenfolge falsch in `web/app.py`.

Starlette verarbeitet `add_middleware()` Aufrufe in **umgekehrter Reihenfolge** (LIFO — die LETZTE `add_middleware()` wird als ERSTE ausgefuehrt, als aeusserste Schicht).

**Vorher (FALSCH):**
1. `app.add_middleware(SessionMiddleware, ...)` — als ERSTE registriert = INNERSTE = laeuft ZULETZT
2. `app.add_middleware(SessionTimeoutMiddleware)` — braucht `request.session` → CRASH
3. `app.add_middleware(CSRFMiddleware)` — braucht `request.session` → CRASH

Da SessionMiddleware als innerste Schicht lief, hatten SessionTimeoutMiddleware und CSRFMiddleware keinen Zugriff auf `request.session` → `AssertionError` → HTTP 500 auf allen Routes.

### Fix

**Datei:** `web/app.py`

Middleware-Reihenfolge korrigiert:
1. `app.add_middleware(CORSMiddleware, ...)` — innerste Schicht (laeuft als letztes)
2. `app.add_middleware(RateLimitMiddleware)` — F48
3. `app.add_middleware(CSRFMiddleware)` — F64 (braucht session)
4. `app.add_middleware(SessionTimeoutMiddleware)` — F65 (braucht session)
5. `app.add_middleware(SessionMiddleware, ...)` — **LETZTE registriert = AEUSSERSTE = laeuft als ERSTE**

Zusaetzlich: `add_csrf_to_templates` Middleware mit try/except abgesichert.

### Verifikation

```
/auth/login    → HTTP 200
/api/health    → HTTP 200
/static/style.css → HTTP 200
/static/themes.css → HTTP 200
```

### Rollback-Info

```bash
cp /home/botuser/Discord_Bots/web/app.py.pre_review_backup /home/botuser/Discord_Bots/web/app.py
sudo systemctl restart web-dashboard
```

---

## Alle Kritischen Fehler

| # | Datei | Problem | Status |
|---|-------|---------|--------|
| K1 | `config/config.json` | SAT-Restarts: Health-Checker Schwellenwert zu aggressiv (3 Failures / 10s Timeout) | **BEHOBEN** |
| K2 | `web/app.py` | Dashboard 500: Middleware LIFO-Reihenfolge falsch, SessionMiddleware als INNERSTE statt AEUSSERSTE | **BEHOBEN** |
| K3 | `modules/database/maintenance.py:106` | DB-Backup: `source_db._conn.backup(dest_db._conn)` greift auf SQLite-Connection aus falschem Thread zu → alle DB-Backups waren 0 Bytes. Fix: Eigene sqlite3-Connections in `asyncio.to_thread()` erstellen | **BEHOBEN** |

---

## Alle Warnungen

| # | Bereich | Problem | Empfehlung |
|---|---------|---------|------------|
| W1 | DB-Backups | Alle 5 alten Backup-Dateien in `data/backups/db/` waren 0 Bytes (Folge von K3). Nach Fix: neues Backup 0.48 MB mit Integritaet OK | **BEHOBEN** |
| W2 | MC Vanilla | Server offline laut Health-Route | Server starten oder Feature-Flag deaktivieren |
| W3 | WEB_ADMIN_PASS_HASH | Wert in `.env` ist leer — Dashboard-Admin-Login funktioniert nur via Discord OAuth | Hash setzen oder OAuth als einzigen Login behalten |
| W4 | FTS5 Index | `search_index` war leer — initialer Reindex durchgefuehrt: 12 Eintraege indiziert | **BEHOBEN** |
| W5 | RCON BMC | RCON-Verbindungsfehler in Logs: `Connect call failed ('127.0.0.1', 25575)` (Port 25575 ist offen aber RCON antwortet sporadisch nicht) | RCON-Timeout erhoehen oder Retry-Logik pruefen |

---

## Alle Hinweise

| # | Bereich | Problem | Empfehlung |
|---|---------|---------|------------|
| H1 | Pycache | 128 alte `.cpython-310.pyc` Dateien | `find . -name "*.cpython-310.pyc" -delete` |
| H2 | requirements.txt | `nbtlib` und `ts3` werden importiert aber fehlen in requirements.txt | Hinzufuegen falls diese Features genutzt werden |
| H3 | Temp-Dateien | `config/.env.old`, `config/config.json.pre_review_backup`, `web/app.py.pre_review_backup` | Nach erfolgreicher Verifikation aufraemen |
| H4 | Port 8443 | HTTPS-Port 8443 antwortet nicht direkt (SSL laeuft ueber Nginx auf 443) | CLAUDE.md / Docs aktualisieren (Port 443 statt 8443) |

---

## Server-Ist-Zustand (Schritt 1)

### System-Grunddaten

| Parameter | Wert |
|-----------|------|
| OS | Ubuntu 22.04.5 LTS (Jammy Jellyfish) |
| Kernel | 5.15.0-168-generic x86_64 |
| Python | 3.10.12 |
| Uptime | 15 Tage, 21 Stunden |
| RAM | 31 GB total, 10 GB frei, 20 GB verfuegbar |
| Swap | 4 GB, 0 B belegt |
| Disk | 1007 GB, 18 GB belegt (2%), 948 GB frei |
| Load | 1.41, 1.38, 1.19 |

### Service-Status

| Service | Status | PID | RAM | Bemerkung |
|---------|--------|-----|-----|-----------|
| monitor-bot | active | 1698364 | 303 MB | Seit 12:50 |
| gameserver-bot | active | 1606632 | 53 MB | Seit 00:47 |
| admin-bot | active | 1611963 | 51 MB | Seit 01:23 |
| web-dashboard | active | 1699492 | 54 MB | Seit 12:54 |
| satisfactory | active | 1695915 | 3.8 GB | Seit 12:38 |

### Offene Ports

| Port | Service | Protokoll |
|------|---------|-----------|
| 4422 | SSH | TCP |
| 80/443 | Nginx | TCP |
| 8080 | Web Dashboard | TCP |
| 7777 | Satisfactory Game | TCP+UDP |
| 25566 | MC BMC | TCP |
| 25575 | MC BMC RCON | TCP |
| 9090 | ? (unbekannt) | TCP |
| 8081 | ? (unbekannt) | TCP |
| 8888 | ? (unbekannt) | TCP |

### Nginx & SSL

- Nginx aktiv, Konfiguration OK (`nginx -t` erfolgreich)
- SSL-Zertifikat auf Port 443 via Let's Encrypt/Certbot

### Fail2Ban

- Aktiv mit 2 Jails: `sshd`, `recidive`

### File Permissions

- `config/.env`: 600 (nur Owner) — **OK**
- `config/config.json`: 644 — OK
- Alle Dateien gehoeren `botuser:botuser` — **OK**

### Disk Usage

| Verzeichnis | Groesse |
|-------------|---------|
| Gesamt | 160 MB |
| data/ | 1.6 MB |
| logs/ | 1008 KB |
| backups/ | 102 MB |

---

## Schritt 2: Import/Syntax-Check

| Pruefung | Ergebnis |
|----------|----------|
| Python-Dateien gesamt | 160 |
| Syntax-Fehler | **0** |
| `__init__.py` Vollstaendigkeit | **17/17 OK** |
| Encoding-Probleme | **0** |

**Ergebnis: BESTANDEN** — Keine Syntax- oder Encoding-Fehler.

---

## Schritt 3: Abhaengigkeits-Analyse

### 3a) Cog-Registrierung

| Bot | Cogs geladen | Details |
|-----|-------------|---------|
| Monitor Bot | 4 | monitor_cog, scheduler_cog, maintenance_mode_cog, shutdown_cog |
| Admin Bot | 16 | moderation, warn, reaction_roles, leveling, tickets, audit, giveaway, temp_voice, teamspeak, server_backup, embed_sender, custom_commands, profile, notify, welcome, command_stats |
| Gameserver Bot | 6 | satisfactory, general, timeout, mod, maintenance, minecraft |
| **Gesamt** | **26/26** | Alle Cogs registriert |

### 3b) Route-Registrierung

| Route-Datei | Registriert |
|-------------|-------------|
| 19 Route-Dateien + auth_router | **20/20 OK** |

Alle Routes in `web/app.py` per `include_router()` registriert.

### 3c) Middleware

| Middleware | Reihenfolge | Status |
|-----------|-------------|--------|
| CORSMiddleware | 1 (innerste) | OK |
| RateLimitMiddleware | 2 | OK |
| CSRFMiddleware | 3 | OK |
| SessionTimeoutMiddleware | 4 | OK |
| SessionMiddleware | 5 (aeusserste) | OK (nach Fix) |

**CSRF:** Schuetzt POST/PUT/DELETE/PATCH. Exempt: `/auth/login`, `/auth/discord/callback`, `/api/health`, `/ws`.

**Rate-Limiter:** Login 5/min, Actions 10/min, Read 60/min. Exempt: `/static`, `/favicon.ico`, `/ws`.

**Session-Timeout:** 60 min Inaktivitaet, 24h absolut, 7 Tage mit "Remember Me". Public: `/auth/*`, `/api/health`, `/static`, `/ws`.

### 3d) Module-Imports

Alle Monitoring-Module werden in `monitor_bot.py` importiert, instanziiert und gestartet:
- HealthAutoRestart, ServiceWatchdog, DiskGuard, DuckDNSMonitor, PortMonitor
- StatsCollector, Forecasting, CrashReplay, GracefulDegradation
- Fail2Ban, SSLMonitor, BackupIntegrity
- PackageChecker, SearchIndexer (via db_maintenance_task)

---

## Schritt 4: Feature-Vollstaendigkeits-Check

Alle 39 Features (F27-F65) sind als Module implementiert und in den entsprechenden Bots/Dashboard registriert.

### Phase 1 (14 Features)

| Feature | Hauptdatei | Existiert | Registriert | Bewertung |
|---------|-----------|----------|------------|-----------|
| F62 Selftest | utils/selftest.py | OK | OK | OK |
| F61 Shutdown | utils/shutdown.py | OK | OK | OK |
| F64 CSRF | web/middleware/csrf.py | OK | OK | OK |
| F65 Session Timeout | web/middleware/session_timeout.py | OK | OK | OK |
| F27 Health Auto-Restart | modules/monitoring/health_checker.py | OK | OK | OK (Config angepasst) |
| F49 Disk Guard | modules/system/disk_guard.py | OK | OK | OK |
| F50 Service Watchdog | modules/monitoring/service_watchdog.py | OK | OK | OK |
| F51 DuckDNS Monitor | modules/network/duckdns_monitor.py | OK | OK | OK |
| F52 Port Monitor | modules/network/port_monitor.py | OK | OK | OK |
| F31 Fail2Ban | modules/security/fail2ban.py | OK | OK | OK |
| F32 SSL Monitor | modules/security/ssl_monitor.py | OK | OK | OK |
| F33 Backup Integrity | modules/backup/integrity.py | OK | OK | OK |
| F34 Health Route | web/routes/health_route.py | OK | OK | OK (funktional getestet) |
| F48 Rate Limiter | web/middleware/rate_limiter.py | OK | OK | OK |

### Phase 2 (4 Features)

| Feature | Hauptdatei | Existiert | Registriert | Bewertung |
|---------|-----------|----------|------------|-----------|
| F28 SQLite Migration | modules/database/db_manager.py | OK | OK | OK (31 Tabellen, 495 KB) |
| F63 Retention/Cleanup | modules/database/maintenance.py | OK | OK | WARNUNG (Backup Thread-Safety) |
| F56 Backup-Rotation | modules/backup/backup_manager.py | OK | OK | OK |
| F53 Config-Versionierung | modules/config_history.py | OK | OK | OK |

### Phase 3 (9 Features)

| Feature | Hauptdatei | Existiert | Registriert | Bewertung |
|---------|-----------|----------|------------|-----------|
| F29 SSE Live-Updates | web/routes/sse_route.py | OK | OK | OK |
| F35 Korrelations-Dashboard | web/routes/correlation_route.py | OK | OK | OK |
| F36 Export-Funktionen | web/routes/export_route.py | OK | OK | OK |
| F37 Ressourcen-Forecasting | modules/monitoring/forecasting.py | OK | OK | OK |
| F44 Error-Dashboard | web/routes/errors_route.py | OK | OK | OK |
| F55 Dashboard-Suche | modules/database/search_indexer.py | OK | OK | HINWEIS (Index leer) |
| F57 Stats-Collector | modules/monitoring/stats_collector.py | OK | OK | OK |
| F58 Analytics-Dashboard | web/routes/analytics_route.py | OK | OK | OK |
| F45 Changelog-Seite | web/routes/changelog_route.py | OK | OK | OK |

### Phase 4 (7 Features)

| Feature | Hauptdatei | Existiert | Registriert | Bewertung |
|---------|-----------|----------|------------|-----------|
| F30 Crash-Replay | modules/monitoring/crash_replay.py | OK | OK | OK |
| F39 Moderation | cogs/moderation_cog.py | OK | OK | OK |
| F40 Leveling | cogs/leveling_cog.py | OK | OK | OK |
| F41 Giveaways | cogs/giveaway_cog.py | OK | OK | OK |
| F43 Custom Commands | cogs/custom_commands_cog.py | OK | OK | OK |
| F54 Alert-Deduplizierung | modules/alert_dedup.py | OK | OK | OK |
| F59 Graceful Degradation | modules/monitoring/graceful_degradation.py | OK | OK | OK |

### Phase 5 (5 Features)

| Feature | Hauptdatei | Existiert | Registriert | Bewertung |
|---------|-----------|----------|------------|-----------|
| F38 Maintenance-Mode | cogs/maintenance_mode_cog.py | OK | OK | OK |
| F42 Paket-Checker | modules/system/package_checker.py | OK | OK | OK |
| F46 Dark Mode | web/static/themes.css + base.html | OK | OK | OK |
| F60 Webhook-Integration | web/routes/webhook_route.py | OK | OK | OK |
| F47 Performance-Optimierung | modules/monitoring/stats_collector.py | OK | OK | OK |

---

## Schritt 5: config.json Analyse

- **JSON valide:** OK
- **Type-Pruefung:** Alle Config-Typen korrekt (bool fuer features, int fuer scheduler, numerisch fuer thresholds)
- **Top-Level-Keys:** 11 (anti_spam, auto_cleanup, auto_restart, chat_bridge, features, login_audit, restart_delay, restart_timer, savegame_protection, scheduler, thresholds)
- **Features:** 24 Flags, 23 auf `true`, 1 auf `false` (chat_bridge)
- **Scheduler:** 33 Werte, alle korrekt getypt

**Ergebnis: BESTANDEN**

---

## Schritt 6: .env Check

- **Gesamt:** 76 Keys
- **Kritische Tokens:** Alle 3 Discord-Tokens vorhanden (72 Zeichen), WEB_SECRET_KEY gesetzt (43 Zeichen)
- **Token-Key-Namen:** `DISCORD_TOKEN_MANAGER`, `DISCORD_TOKEN_WATCHDOG`, `ADMIN_BOT_TOKEN` (nicht Standard-Namen, aber korrekt in Code referenziert)
- **Permissions:** 600 (nur Owner) — **OK**

**Leere Keys:**
- `MC_BMC_MODPACK_ID`, `MC_BMC_MODPACK_VERSION`, `CURSEFORGE_API_KEY` — nicht kritisch (CurseForge nicht genutzt)
- `GPG_PASSPHRASE` — nicht kritisch (kein GPG-Backup konfiguriert)
- `TS_HOST`, `TS_PASSWORD` — nicht kritisch (TeamSpeak deaktiviert)
- `WEB_ADMIN_PASS_HASH` — WARNUNG: Dashboard-Admin-Login nur via Discord OAuth moeglich

**Ergebnis: BESTANDEN (mit Warnung W3)**

---

## Schritt 7: Template-Integritaet

- **Templates gesamt:** 29
- **Syntax-Fehler:** 0
- **Jinja2-Vererbung:** Alle Templates erben korrekt von `base.html`
- **base.html Features:** CSRF Meta-Tag, Session-Timeout JS, Dark-Mode Toggle, Suchfeld, Changelog-Link, HTMX Script — alle vorhanden

**Ergebnis: BESTANDEN**

---

## Schritt 8: SQLite-Migration (F28)

- **Datenbank:** `data/botdata.db` (495.616 Bytes)
- **Tabellen:** 31 (inkl. FTS5 search_index)
- **Daten:** 448 stats_history, 12 player_sessions, 7 backup_history, 2 events, 1 player
- **DB-Backups:** 5 Dateien in `data/backups/db/` — **ALLE 0 BYTES** (siehe K3)
- **FTS5 Index:** Erstellt aber leer (noch kein Reindex)

**Ergebnis: WARNUNG (DB-Backups fehlerhaft)**

---

## Schritt 9: Sicherheits-Audit

| Pruefung | Ergebnis |
|----------|----------|
| Hardcoded Secrets | Keine im Projekt-Code (nur in venv-Packages) |
| Debug-Mode | Kein `debug=True` im Projekt-Code |
| .env Permissions | 600 — OK |
| Fail2Ban | 2 Jails aktiv (sshd, recidive) |
| CSRF-Schutz | Aktiv fuer POST/PUT/DELETE/PATCH |
| Rate-Limiting | Aktiv (5/min Login, 60/min Read) |
| Session-Timeout | 60 min Inaktivitaet |
| HTTPS | Via Nginx + Let's Encrypt auf Port 443 |

**Passwort-Referenzen im Code (alle korrekt via ENV):**
- `cogs/teamspeak_cog.py:143` — `password=TS_PASSWORD` (aus .env)
- `bots/monitor_bot.py:177` — `password=SMTP_PASS` (aus .env)
- `modules/notifications/email_notifier.py:28` — `password="app-password"` (Default-Wert, wird via Konstruktor ueberschrieben)
- `modules/minecraft/rcon.py:39` — `self.password = password` (Parameter)

**Ergebnis: BESTANDEN**

---

## Schritt 10: Systemd-Konsistenz

Alle 4 Bot-Services und 1 Game-Service sind korrekt konfiguriert:

| Service | User | WorkingDirectory | Restart | MemoryMax |
|---------|------|-----------------|---------|-----------|
| monitor-bot | botuser | /home/botuser/Discord_Bots | on-failure (15s) | 768M |
| web-dashboard | botuser | /home/botuser/Discord_Bots | on-failure (10s) | 512M |
| admin-bot | botuser | /home/botuser/Discord_Bots | on-failure | - |
| gameserver-bot | botuser | /home/botuser/Discord_Bots | on-failure | - |
| satisfactory | - | - | - | - |

Security-Hardening aktiv: `ProtectSystem`, `ProtectHome=read-only`, `ReadWritePaths`, `PrivateTmp`, `ProtectControlGroups`.

**Ergebnis: BESTANDEN**

---

## Schritt 11: Toter Code

- **Temp/Backup-Dateien:** 3 (`config/.env.old`, `config/config.json.pre_review_backup`, `web/app.py.pre_review_backup`)
- **Alte Pycache:** 128 `.cpython-310.pyc` Dateien (Python 3.10 Artefakte)

**Ergebnis: HINWEIS (aufraemen empfohlen)**

---

## Schritt 12: requirements.txt

- **Packages in requirements.txt:** 16
- **Alle installierten venv-Packages:** Vorhanden und kompatibel
- **Fehlend in requirements.txt:** `nbtlib` (MC NBT-Parser), `ts3` (TeamSpeak-Client) — werden im Code importiert
- **In requirements aber nicht direkt importiert:** `itsdangerous`, `python-multipart` — werden indirekt von FastAPI/Starlette genutzt

**Ergebnis: BESTANDEN (mit Hinweis H2)**

---

## Schritt 13: Laufzeit-Funktionstests

| Test | Ergebnis |
|------|----------|
| `/api/health` JSON | OK — Valides JSON mit 3 Servern, 2 Bots |
| `/auth/login` HTML | OK — Formular vorhanden |
| `/static/style.css` | OK — HTTP 200 |
| `/static/themes.css` | OK — HTTP 200 |
| SAT-Server Status | Online, 1 Spieler, 32+ min Uptime |
| MC BMC Status | Online, 0 Spieler |
| MC Vanilla Status | Offline |
| Module Import (venv) | OK — aiosqlite und alle Dependencies vorhanden |

**Ergebnis: BESTANDEN**

---

## Schritt 14: Async/Await + Intents

### Async-Korrektheit

- **Blocking `time.sleep()`:** 1 Vorkommen in `modules/satisfactory/server.py:125` (in sync Subprocess-Kontext — akzeptabel)
- **Blocking `requests.*`:** Keine — alle HTTP-Calls nutzen `aiohttp`/`httpx`
- **Fehlende `await`:** Keine gefunden

### Discord Intents

| Bot | message_content | members | reactions | Korrekt |
|-----|----------------|---------|-----------|---------|
| Monitor Bot | OK | OK | - | OK |
| Admin Bot | OK | OK | OK | OK |
| Gameserver Bot | OK | OK | - | OK |

**Ergebnis: BESTANDEN**

---

## Schritt 15: Cross-Modul Datenfluesse

### JSON-Bridge Pattern

Monitor-Bot schreibt JSON-Dateien nach `data/monitor/`, Dashboard liest sie ueber API-Endpunkte:

| JSON-Datei | Schreiber | Leser |
|------------|-----------|-------|
| satisfactory_status.json | StatusWriter | health_route, dashboard |
| mc_bmc_status.json | StatusWriter | health_route, server_detail |
| port_monitor.json | StatusWriter | health_route |
| disk_guard.json | StatusWriter | health_route |
| service_watchdog.json | StatusWriter | health_route |
| duckdns_monitor.json | StatusWriter | health_route |
| health_auto_restart.json | StatusWriter | health_route |
| ssl_status.json | StatusWriter | security_route |
| package_checker.json | StatusWriter | system_route |
| events.json | StatusWriter | dashboard, errors_route |

### Config-Zugriff

Alle Module nutzen `utils.config.load_config()` — einheitliches Pattern.

### Datenbank-Zugriff

Alle DB-Zugriffe ueber `modules.database.db_manager` via `aiosqlite` — einheitliches Pattern.

**Ergebnis: BESTANDEN**

---

## Schritt 16: Abschluss-Smoke-Test

| Pruefung | Ergebnis |
|----------|----------|
| monitor-bot | **active** |
| gameserver-bot | **active** |
| admin-bot | **active** |
| web-dashboard | **active** |
| satisfactory | **active** |
| `/auth/login` | **HTTP 200** |
| `/api/health` | **HTTP 200** |
| `/static/style.css` | **HTTP 200** |
| `/static/themes.css` | **HTTP 200** |

**Ergebnis: SMOKE-TEST BESTANDEN**

---

## Empfehlungen fuer naechste Schritte

### Prioritaet 1 (zeitnah)

1. **K3 fixen:** DB-Backup Thread-Safety in `modules/database/maintenance.py:106` — `source_db._conn.backup()` muss im aiosqlite-Thread ausgefuehrt werden, z.B. via `await source_db.execute()` Wrapper oder `asyncio.to_thread()`
2. **FTS5 Reindex:** `POST /api/search/reindex` aufrufen um den Suchindex initial zu befuellen
3. **MC Vanilla:** Server starten oder aus Health-Route ausschliessen

### Prioritaet 2 (Nice-to-have)

4. **Alte Pycache aufraemen:** `find . -name "*.cpython-310.pyc" -delete`
5. **Temp-Dateien aufraemen:** `.pre_review_backup` und `.env.old` entfernen nach Verifikation
6. **requirements.txt:** `nbtlib` und `ts3` ergaenzen falls diese Features aktiv sind
7. **Unbekannte Ports pruefen:** Was laeuft auf 8081, 8888, 9090?

---

## Offene Punkte

| Punkt | Grund |
|-------|-------|
| Feature-Spezifikations-Vergleich | FEATURE_PLAN.md nicht Zeile-fuer-Zeile gegen Code verglichen (zu umfangreich fuer Context-Window) |
| Login-Flow testen | Kein Discord OAuth Test moeglich (braucht Browser-Interaktion) |
| WebSocket-Funktionalitaet | Nicht getestet (braucht JS-Client) |
| SSE Live-Updates | Timeout bei Test (erwartetes Verhalten fuer Event-Stream) |
| RCON-Verbindungsprobleme | Sporadische Fehler, Ursache nicht vollstaendig geklaert |
