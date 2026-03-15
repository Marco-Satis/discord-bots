# Discord Bot System — Cowork Anweisungen

> **Version:** 4.0.1 | **Stand:** 15. Maerz 2026
> **Zweck:** Lokale Datei-Arbeit — Integration der Auto-Update-Module
> **Spezifikation:** `docs/FEATURE_PLAN_AUTO_UPDATE.md` (Referenz fuer Feature-Details)
> **Projekt-Doku:** `docs/Projektdokumentation_v4.0.0.md` (Architektur-Referenz)

---

## Rolle & Grenzen

Du arbeitest im Projektordner des Discord Bot Systems v4.0.1. Deine Aufgaben sind **ausschliesslich lokale Datei-Operationen:**

- Code schreiben, lesen und aendern (Python, Markdown, Config)
- Bugfixes in bestehenden Modulen
- Neue Module und Dateien erstellen
- Dokumentation aktualisieren
- Plaene ausarbeiten und verfeinern

**Du hast KEINEN Server-Zugriff.** Kein SSH, kein SCP, kein systemctl, kein journalctl. Server-Deployment und Live-Tests werden separat mit Claude Code durchgefuehrt.

**Lies NICHT die Datei `CLAUDE.md`.** Diese enthaelt Anweisungen fuer Claude Code (Server-Deployment) und ist fuer dich nicht relevant. Deine Anweisungen stehen hier in `COWORK.md`.

**Sprache:** Deutsch (Code-Kommentare, Docs, Variablennamen englisch).

---

## Projekt-Kontext

Das Discord Bot System ist ein 3-Bot-System (GameServer Bot, Monitor Bot, Admin Bot) mit Web-Dashboard fuer Satisfactory + Minecraft Server-Management. Aktuell in Arbeit: ein automatisches Update-System fuer MC-Modpacks (CurseForge API) und Satisfactory (SteamCMD).

**Zentrale Referenz-Dokumente:**

| Dokument | Wann lesen? |
|----------|-------------|
| `docs/FEATURE_PLAN_AUTO_UPDATE.md` | Feature-Spezifikation — abschnittsweise lesen, nicht komplett! |
| `docs/Projektdokumentation_v4.0.0.md` | Architektur, Module, Middleware, Datenbank |
| `PROGRESS.md` | Aktueller Arbeitsstand |
| `docs/REVIEW_v4.0.0.md` | System-Zustand nach letztem Review |

**Context-Window-Strategie:** FEATURE_PLAN_AUTO_UPDATE.md ist 48 KB gross. Lade NIE das ganze Dokument auf einmal. Nutze gezielte Abschnitte:
- Architektur/UpdateManager: Abschnitt 2
- Update-Zeitplan: Abschnitt 3
- Update-Ablauf (Phasen): Abschnitt 4
- CurseForge API: Abschnitt 4 (zweiter)
- In-Game-Befehle: Abschnitt 5
- Spielererkennung: Abschnitt 6
- Discord-Commands: Abschnitt 7
- DB-Schema: Abschnitt 8
- Neue/Geaenderte Dateien: Abschnitt 9
- Backup-Regeln: Abschnitt 10
- DM an Owner: Abschnitt 14
- SAT Auto-Update: Abschnitt 15

---

## Offene Aufgaben: Auto-Update Integration (9 Stueck)

Die Kern-Module sind fertig geschrieben (siehe unten). Was fehlt ist die **Integration** in die bestehenden Bots und Cogs. Jede Aufgabe verweist auf den relevanten Feature-Plan-Abschnitt.

### I1: monitor_bot.py Integration
**Datei:** `bots/monitor_bot.py`
**Feature-Plan:** §2 (UpdateManager), §4 Phase 0 (Crash-Recovery), §6 (Voice-Channel)
**Aufgaben:**
- UpdateManager importieren und pro MC-Server instanziieren
- `check_and_resume()` in `on_ready` aufrufen (Crash-Recovery)
- Chat-Bridge: Referenzen auf UpdateManager + MinecraftServer uebergeben
- Voice-Channel-Format von "X Online" auf "X/Y Online" aendern (get_player_count gibt jetzt Tuple zurueck)

### I2: Scheduler Integration
**Datei:** `cogs/scheduler_cog.py`
**Feature-Plan:** §3 (Zeitplan), §10 (Backup/Retention), §15 (SAT-Zeitplan)
**Aufgaben:**
- Update-Checks um 12:00 und 00:00 einbauen (ModpackUpdater.check_for_updates)
- Daily-Restart (04:00) mit Update-Integration verbinden (00:00-Check markiert → 04:00 fuehrt aus)
- SAT-Updater in gleichen Zeitplan (12:00/00:00 SteamCMD Build-ID Check)
- Retention-Cleanup fuer Rollback-Ordner + Server Pack ZIPs (2 Versionen behalten)

### I3: Chat-Bridge In-Game-Befehle
**Datei:** `modules/minecraft/chat_bridge.py`
**Feature-Plan:** §5 (komplett — Code-Beispiele, Regex, Befehle, tellraw)
**Aufgaben:**
- COMMAND_RE Regex hinzufuegen (nutzt bestehendes _TS + _TH)
- In `_process_log_content()` nach Chat-Check: Command-Matching
- `_handle_ingame_command(player, command, args)` implementieren
- `_is_op(player)` via ops.json des jeweiligen Servers
- 8 Befehle: !status, !version, !players, !tps, !cancel, !restart, !backup, !help
- Antworten via RCON `/tellraw @a` (formatiert mit Farben) oder `/say`
- Neue Constructor-Parameter: MinecraftServer-Referenz + UpdateManager-Referenz + server_path

### I4: Spielererkennung-Fix
**Datei:** `modules/minecraft/server.py`
**Feature-Plan:** §6 (komplett — Regex, Fallback, Dual-Erkennung)
**Aufgaben:**
- `get_player_count()` Regex-Fix: 3 Formate matchen ("X of a max of Y", "X of max Y", "X/Y")
- `_get_max_players_fallback()` aus server.properties (nutzt bestehendes `get_properties()`)
- Rueckgabe als Tuple `(online, max)` statt nur `online`

### I5: Discord-Notifier DM
**Datei:** `modules/notifications/discord_notifier.py`
**Feature-Plan:** §14 (komplett — Code-Beispiel)
**Aufgaben:**
- `send_dm_to_owner(title, description, level, fields)` Methode
- Liest BOT_OWNER_ID aus ENV
- Embed mit Farbe je nach NotifyLevel
- Fehlerbehandlung: Forbidden (DMs deaktiviert) → Fallback auf Channel

### I6: restart_timer.py Anpassung
**Datei:** `modules/restart_timer.py`
**Feature-Plan:** §2 (MCCountdownTimer erbt von RestartTimer)
**Aufgaben:**
- `_send_ingame_warning(message, seconds_remaining)` als eigene Methode extrahieren
- Aktuell ist die In-Game-Warnung inline im Countdown-Loop
- MCCountdownTimer (bereits geschrieben) ueberschreibt diese Methode fuer RCON /title

### I7: Discord-Commands MC
**Datei:** `cogs/monitor_cog.py` oder neues Cog `cogs/update_cog.py`
**Feature-Plan:** §7 (Command-Tabelle + Beispiel-Embed)
**Aufgaben:**
- `/mc modpack status [server]` — Version, Update-Status, letzter Check
- `/mc modpack update [server]` — Manuelles Update mit 10min Countdown (Admin)
- `/mc modpack cancel` — Laufendes Update/Countdown abbrechen (Admin)
- `/mc modpack rollback [server]` — Manueller Rollback (Owner)
- `/mc modpack history [server]` — Update-Historie aus SQLite (Spieler)
- `/mc modpack check [server]` — Sofortiger Check ohne Auto-Update (Admin)

### I8: Discord-Commands SAT
**Datei:** `cogs/satisfactory_cog.py`
**Feature-Plan:** §7, §15 (SAT-Abbruch)
**Aufgaben:**
- `/sat update cancel` — Laufendes SAT-Update/Countdown abbrechen (Admin)

### I9: ENV-Dokumentation
**Datei:** `config/.env.example`
**Feature-Plan:** §9 (ENV-Variablen Liste)
**Aufgaben:**
- Alle neuen ENV-Variablen dokumentieren:
  - CURSEFORGE_API_KEY
  - MC_BMC_MODPACK_SOURCE, MC_BMC_CURSEFORGE_PROJECT_ID, MC_BMC_CURSEFORGE_FILE_ID
  - MC_BMC_MODPACK_VERSION, MC_BMC_PRESERVE_FILES
  - MC_VANILLA_* (Platzhalter)
  - BOT_OWNER_ID

---

## Empfohlene Reihenfolge

Die Aufgaben haben Abhaengigkeiten. Empfohlene Reihenfolge:

1. **I6** (restart_timer.py) — Voraussetzung fuer MCCountdownTimer
2. **I4** (server.py Spielererkennung) — Voraussetzung fuer Voice-Channel "X/Y"
3. **I5** (discord_notifier.py DM) — Voraussetzung fuer Fehler-Benachrichtigung
4. **I1** (monitor_bot.py) — Haengt von I4 + I6 ab
5. **I3** (chat_bridge.py In-Game) — Haengt von I1 ab (braucht Referenzen)
6. **I2** (scheduler_cog.py) — Haengt von I1 ab (braucht UpdateManager)
7. **I7** (Discord-Commands MC) — Haengt von I1 ab
8. **I8** (Discord-Commands SAT) — Unabhaengig
9. **I9** (.env.example) — Unabhaengig, kann jederzeit

---

## Offene Bugs (niedrigere Prioritaet)

Diese Bugs wurden in frueheren Sessions identifiziert. Bei Gelegenheit oder wenn Marco sie anspricht, fixen.

### BUG-1: SAT CPU/RAM zeigt 0 im Dashboard (PRIO 2)

**Problem:** Satisfactory Detail-Seite zeigt CPU: 0% und RAM: 0 MB.
**Ursache:** `psutil.Process(pid)` wirft `AccessDenied` weil der botuser den satisfactory-User-Prozess nicht lesen darf. Ein `/proc`-Fallback in `modules/satisfactory/server.py` funktioniert noch nicht korrekt.
**Datei:** `modules/satisfactory/server.py`
**Naechster Schritt:** Den `/proc`-Fallback debuggen — wahrscheinlich Parsing-Problem bei `/proc/<pid>/stat` (CPU) oder `/proc/<pid>/status` (VmRSS).

### BUG-2: Spieler-Online-Chart moeglicherweise leer (PRIO 3)

**Problem:** Nach StatsCollector-Fix (bot_status.json-Filter) nicht verifiziert ob der Chart korrekt Daten anzeigt.
**Datei:** `modules/monitoring/stats_collector.py`, Dashboard-Templates
**Naechster Schritt:** Code-Review ob stats_collector.py Spielerzahlen korrekt liest und in stats_history schreibt.

### BUG-3: RCON BMC sporadische Verbindungsfehler (PRIO 3)

**Problem:** RCON-Verbindung zu MC BMC (Port 25575) schlaegt gelegentlich fehl. Timeout bereits auf 10s erhoeht, Retry-Logik (2 Versuche) vorhanden.
**Datei:** `modules/minecraft/rcon.py`, `modules/minecraft/server.py`
**Naechster Schritt:** Connection-Pooling oder Keep-Alive evaluieren.

### BUG-4: MC Vanilla Server offline (PRIO 3)

**Problem:** MC Vanilla ist offline. Entscheidung noetig: Starten oder aus Monitoring ausschliessen.
**Naechster Schritt:** Feature-Flag in config.json vorbereiten falls gewuenscht.

### BUG-5: Unbekannte offene Ports (PRIO 3)

**Problem:** Ports 8081, 8888, 9090 sind offen. Erfordert Server-Zugriff — nur als Erinnerung.

---

## Gelernte Lektionen (aus BMC5-Migration)

1. **systemd ReadWritePaths bei Pfad-Migrationen pruefen!** Der Monitor-Bot crash-loopte weil `monitor-bot.service` noch auf `/home/minecraft/bettermc` statt `/home/minecraft/bmc5` zeigte.
2. **NeoForge nutzt `run.sh`**, nicht direkt eine JAR. Relevant fuer `neoforge_updater.py`.
3. **Modpack-ZIPs ueberschreiben `server.properties`** — nach Entpacken IMMER Custom-Dateien wiederherstellen. Das Auto-Update-System macht das in Phase 5/6.
4. **NeoForge Log-Format** unterscheidet sich von Vanilla. Die Chat-Bridge-Regex matchen bereits beide Formate.
5. **rcon-cli v0.10.3** nutzt `-a host:port -p passwort` (nicht `--host`/`--port`).

---

## Bestehende Auto-Update-Module (lokal vorhanden, fertig)

| Datei | Groesse | Status |
|-------|---------|--------|
| `modules/minecraft/update_manager.py` | 31 KB | Fertig |
| `modules/minecraft/file_manager.py` | 23 KB | Fertig |
| `modules/minecraft/modpack_updater.py` | 14 KB | Fertig (CurseForge API) |
| `modules/minecraft/mc_countdown.py` | 10 KB | Fertig (erbt von RestartTimer) |
| `modules/minecraft/neoforge_updater.py` | 10 KB | Fertig |
| `modules/monitoring/update_checker.py` | — | SAT-Updater perform_update() fertig |
| `modules/database/migrations.py` | — | DB-Migration v3→v4 fertig |

---

## Arbeitsweise

Wenn Marco dir eine Aufgabe gibt:

1. **Lies den relevanten Abschnitt** der Spezifikation (`docs/FEATURE_PLAN_AUTO_UPDATE.md`)
2. **Lies die betroffene(n) Datei(en)** im Projektordner
3. **Fuehre die Aenderung durch** — schreibe/aendere die Datei(en)
4. **Erklaere kurz** was du geaendert hast und warum

Bei groesseren Aenderungen: Erst analysieren, dann Vorschlag machen, dann nach Marcos OK umsetzen.

---

## Projekt-Struktur (wichtigste Ordner)

```
Discord_Bots/
├── bots/                    # Bot-Hauptdateien (monitor_bot.py, admin_bot.py, gameserver_bot.py)
├── cogs/                    # Discord Slash Commands (27 Cogs)
├── modules/
│   ├── minecraft/           # MC-Module (server, rcon, chat_bridge, backup, update_*)
│   ├── satisfactory/        # SAT-Module (server, api_client, blueprints, savegames)
│   ├── monitoring/          # Health-Check, Watchdog, Stats, Forecasting, update_checker
│   ├── database/            # SQLite Manager, Migrations, Maintenance, Search
│   ├── notifications/       # Discord + E-Mail Benachrichtigungen
│   ├── backup/              # Backup-Manager, OneDrive, Integrity
│   ├── security/            # Fail2Ban, SSL Monitor
│   ├── network/             # DuckDNS, Port Monitor
│   └── system/              # Disk Guard, Package Checker
├── utils/                   # Config, Logger, Permissions, Selftest, Shutdown
├── web/                     # Dashboard (FastAPI + HTMX + Jinja2)
│   ├── routes/              # 20 Route-Module
│   ├── middleware/           # CSRF, Rate-Limiter, Session-Timeout
│   ├── templates/           # 30 HTML-Templates
│   └── static/              # CSS, JS, Assets
├── config/                  # config.json, .env, .env.example
├── data/                    # SQLite DB, JSON-Bridge-Dateien, Backups
├── docs/                    # Dokumentation, Feature-Plaene, Reviews
├── CLAUDE.md                # Anweisungen fuer Claude Code (NICHT fuer Cowork!)
├── COWORK.md                # Diese Datei — Anweisungen fuer Cowork
├── PROGRESS.md              # Aktueller Arbeitsstand
├── CHANGELOG.md             # Aenderungshistorie
└── README.md                # Projekt-Uebersicht
```

---

## Technische Details

**Python:** 3.10+ (Server hat 3.10.12)
**Async:** Komplett async/await (asyncio). Blocking Calls in `asyncio.to_thread()`.
**Discord.py:** 2.x mit Slash Commands, Cogs, Intents (Members, Message Content, Reactions)
**Datenbank:** SQLite mit aiosqlite, WAL-Modus, 31 Tabellen. Zugriff via `modules/database/db_manager.py`.
**Config:** `config/config.json` (Feature-Flags, Scheduler) + `config/.env` (Secrets, Tokens)
**Dashboard:** FastAPI + Jinja2, Port 8080. Middleware: Session → Timeout → CSRF → Rate-Limiter → CORS.

---

## Regeln

1. **Lies NICHT `CLAUDE.md`** — das ist fuer Claude Code und enthaelt Server-Anweisungen die dich verwirren
2. **Kein Server-Zugriff** — du kannst keine SSH-Befehle ausfuehren
3. **Bestehenden Code respektieren** — lies Dateien bevor du sie aenderst
4. **FEATURE_PLAN_AUTO_UPDATE.md abschnittsweise** — nie komplett laden
5. **Bei Unklarheiten fragen** — lieber nachfragen als falsch implementieren
6. **Deutsch** — Code-Kommentare und Docs auf Deutsch, Variablen/Funktionen auf Englisch
7. **Error-Handling** — Jede externe Operation (API, File-IO, RCON) braucht try/except
8. **Async** — Keine blockierenden Aufrufe in async Funktionen
9. **Logging** — `logger = logging.getLogger(__name__)` in jedem Modul
10. **Type-Hints** — Funktions-Signaturen mit Type-Hints versehen
