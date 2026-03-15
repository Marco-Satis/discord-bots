# Discord Bot System V3 – Automatische Korrekturen

**Datum:** 2026-02-18
**Durchgeführt von:** Claude Code-Analyse & Auto-Fix

## Zusammenfassung
- 🔴 Kritische Fehler behoben: 12
- 🟡 Logik-Fehler behoben: 8
- 🔵 Code-Qualität verbessert: 52 (13 Cog Handler + 36 Type Hints + 18 Exception Fixes + Sonstiges)
- 🟢 Architektur optimiert: 6

---

## Detaillierte Änderungen

### 🔴 Kritische Fehler

### `utils/config.py`
- **get_env() Fix:** `os.getenv(key, default)` ersetzte den default-Wert falsch wenn cast angegeben war. Jetzt wird `os.getenv(key)` ohne default aufgerufen und der default nur zurückgegeben wenn val is None. Vorher wurde bei `get_env("KEY", 587, cast=int)` der default als zweiter Parameter von os.getenv übergeben statt als Fallback.
- **Docstring hinzugefügt** mit Args/Returns Dokumentation und Type Hint für `key: str`

### `bots/gameserver_bot.py`
- **Import Fix:** `import asyncio` fehlte auf Modul-Ebene, wurde nur in except-Block importiert → An den Anfang verschoben
- **Namespace Iteration Fix:** `for key, value in interaction.namespace:` → `interaction.namespace.__dict__.items()` (discord.py Namespace-Objekt ist nicht direkt iterierbar)
- **Shutdown auf allen Pfaden:** Shutdown-Cleanup wird jetzt auch bei `Exception` ausgeführt, nicht nur bei `KeyboardInterrupt`

### `bots/monitor_bot.py`
- **OWNER_ID Default:** `get_env("OWNER_ID", cast=int)` ohne Default → `get_env("OWNER_ID", default=0, cast=int)` — verhindert RuntimeError wenn nicht gesetzt
- **crash_replay.capture() Await:** Fehlender `await` bei `crash_replay.capture(crash_event.crash_number)` hinzugefügt
- **Shutdown Error Handling:** `asyncio.get_event_loop().run_until_complete(shutdown())` jetzt in try-except gewrappt, Cleanup auch bei Fatal Exceptions

### `config/.env`
- **API_TOKEN Dokumentation:** Kommentar hinzugefügt der erklärt wie der Token über das Satisfactory Admin Panel generiert wird (PasswordLogin → ClaimServer → GenerateAPIToken)
- **API_VERIFY_SSL Kommentar:** Erklärt warum SSL-Verifizierung deaktiviert ist (Self-Signed Certificate)
- **SATISFACTORY_SAVE_PATH** war bereits korrekt vorhanden ✓

### `modules/monitoring/optimizer.py`
- **Security Fix:** `sudo bash -c "echo 3 > ..."` ersetzt durch `sudo /usr/local/bin/drop-caches.sh` — verhindert Shell Injection über bash -c

### `modules/monitoring/crash_replay.py`
- **Async Fix:** `capture()` Methode von sync auf async geändert und File-I/O in Executor ausgelagert — konsistent mit dem await-Aufruf in monitor_bot.py

### `modules/monitoring/player_ip_tracker.py`
- **Shell Injection Fix:** `create_subprocess_shell()` mit IP-String-Interpolation ersetzt durch `create_subprocess_exec()` mit Argument-Listen in `_ufw_block()` und `_ufw_unblock()`
- **IP Validation:** Neuer `_validate_ip()` Helper mit Regex-Prüfung auf gültiges IPv4-Format vor jeder UFW-Kommando-Ausführung
- **Race Condition Fix:** `kick_player()` führt `_ufw_unblock()` nur noch aus wenn `_ufw_block()` erfolgreich war

### `modules/satisfactory/server.py`
- **Command Injection Prevention:** `ALLOWED_ACTIONS` Whitelist (`start`, `stop`, `restart`, `status`, `is-active`) mit Validierung vor jeder systemctl-Ausführung
- **Type Hints:** `_systemctl()` und `_find_process()` Signaturen mit vollständigen Type Hints versehen

---

### 🟡 Logik- & Funktionsfehler

### `modules/mod_manager.py`
- **Fehlender Import:** `import asyncio` hinzugefügt (wurde für `asyncio.TimeoutError` verwendet aber nicht importiert → NameError zur Laufzeit)
- **None-Safety:** `m.get("name").lower()` → `m.get("name", "").lower()` in 3 Methoden (uninstall_mod, update_mod, get_mod_info) — verhindert AttributeError

### `modules/maintenance.py`
- **Nested Event Loop Fix:** `loop.run_in_executor(None, lambda: asyncio.run(connect()))` in `_check_port()` verursachte RuntimeError → Ersetzt durch direktes `asyncio.open_connection()` mit `asyncio.wait_for(timeout=5)`

### `modules/anti_spam.py`
- **Rate Limiting Fix:** Nachrichten-/Command-Limit wird jetzt VOR dem Hinzufügen geprüft — vorher wurde eine Nachricht mehr als erlaubt akzeptiert bevor Spam erkannt wurde
- Betrifft sowohl `check_message()` als auch `check_command()`

### `modules/satisfactory/savegame_analyzer.py`
- **Race Condition Fix:** `asyncio.Lock()` in `__init__` hinzugefügt und `get_stats()` Analyse-Block in `async with self._lock:` gewrappt — verhindert parallele Analysen die den Cache korrumpieren können

### `modules/satisfactory/blacklist.py`
- **Spezifische Exceptions:** `except Exception` in `load()` und `save()` ersetzt durch `(json.JSONDecodeError, FileNotFoundError, IOError)`

### `modules/satisfactory/whitelist.py`
- **Spezifische Exceptions:** Identisch zu blacklist.py — `load()` und `save()` mit spezifischen Exception-Typen

### `modules/satisfactory/blueprint_manager.py`
- **Path Traversal Fix:** `name.split("/")[-1]` ersetzt durch `Path(name).name` — verhindert Directory Traversal bei manipulierten ZIP-Dateien

### `modules/satisfactory/api_client.py`
- **Dokumentation:** SSL-Disable mit erklärendem Kommentar versehen (Self-Signed Certificate des Satisfactory Servers)

---

### 🔵 Code-Qualität

### `modules/satisfactory/chat_bridge.py`
- **PEP 585 Kompatibilität:** `list[str]` → `List[str]` und `list[ChatMessage]` → `List[ChatMessage]` für Python 3.9 Support
- `List` Import aus `typing` hinzugefügt

### Cog Error Handler (13 Dateien)
Jeder der folgenden Cogs erhielt einen einheitlichen `cog_app_command_error` Handler:
- `cogs/satisfactory_cog.py`
- `cogs/general_cog.py`
- `cogs/satisfactory_players_cog.py`
- `cogs/satisfactory_backup_cog.py`
- `cogs/satisfactory_config_cog.py`
- `cogs/satisfactory_blueprints_cog.py`
- `cogs/timeout_cog.py`
- `cogs/chat_bridge_cog.py`
- `cogs/minecraft_cog.py`
- `cogs/blacklist_cog.py`
- `cogs/whitelist_cog.py`
- `cogs/maintenance_cog.py`
- `cogs/mod_cog.py`

**Handler-Pattern:**
- CheckFailure → Ephemeral "Keine Berechtigung" Nachricht
- Andere Fehler → Logging mit exc_info + Ephemeral Fehlermeldung
- Prüft `interaction.response.is_done()` für korrekte Response-Methode

### Type Hints vervollständigt (36 Dateien)
Alle Module haben jetzt vollständige Type Annotations erhalten:

**satisfactory/ (10 Dateien):** server.py, api_client.py, whitelist.py, blacklist.py, blueprint_manager.py, savegame_stats.py, savegame_analyzer.py, settings_backup.py, save_header.py, chat_bridge.py

**monitoring/ (13 Dateien):** health_check.py, performance.py, player_tracker.py, update_checker.py, stats_tracker.py, crash_replay.py, player_ip_tracker.py, login_audit.py, auto_cleanup.py, selftest.py, savegame_protection.py, graceful_degradation.py, steam_changelog.py

**backup/ (3 Dateien):** backup_manager.py, onedrive_backup.py, config_backup.py

**notifications/ (2 Dateien):** discord_notifier.py, email_notifier.py

**Standalone (8 Dateien):** restart_timer.py, word_filter.py, anti_spam.py, chat_bridge.py, command_logger.py, config_validator.py, maintenance.py, mod_manager.py

**Konventionen:**
- `Optional[X]` für nullable Parameter
- `Tuple[bool, str]` statt `tuple[bool, str]` (Python 3.9 Kompatibilität)
- `Dict[str, Any]`, `List[Dict[str, Any]]` für komplexe Datenstrukturen
- `-> None` für void-Funktionen
- `Path` für Dateipfade

### Bare Exceptions durch spezifische Exceptions ersetzt (18 Stellen in 12 Dateien)
Alle `except Exception:` (ohne `as e`) Blöcke wurden korrigiert:
- **minecraft/backup.py:** → `except (IOError, OSError) as e:` + Logging
- **config_validator.py (2x):** → `except (IOError, OSError) as e:` + Logging
- **backup/onedrive_backup.py:** → `except (subprocess.SubprocessError, OSError, asyncio.TimeoutError) as e:` + Logging
- **backup/config_backup.py (2x):** → `except (asyncio.TimeoutError, OSError) as e:` + Logging
- **satisfactory/settings_backup.py:** → `except (json.JSONDecodeError, IOError, OSError, ValueError) as e:` + Logging
- **satisfactory/api_client.py:** → `except (aiohttp.ClientError, asyncio.TimeoutError) as e:` + Logging
- **satisfactory/save_header.py:** → `except (struct.error, ValueError, IOError) as e:` + Logging
- **restart_timer.py (2x):** → `except discord.DiscordException as e:` + Logging
- **satisfactory/savegame_stats.py (2x):** → `except (IOError, OSError, ValueError) as e:` + Logging
- **monitoring/update_checker.py (3x):** → `except (subprocess.SubprocessError, ValueError, OSError, asyncio.TimeoutError) as e:` + Logging
- **monitoring/savegame_protection.py:** → `except (IOError, OSError) as e:` + Logging
- **monitoring/health_check.py:** → `except (OSError, asyncio.TimeoutError) as e:` + Logging

---

### 🟢 Architektur

### v3_optimizer_update Integration
- **optimizer.py:** Bereits korrekt in `modules/monitoring/optimizer.py` vorhanden ✓
- **monitoring/__init__.py:** Importiert bereits `ServerOptimizer` ✓
- **optimize_server.sh:** Von `v3_optimizer_update/` nach `scripts/optimize_server.sh` kopiert
- **botuser-sudoers:** Mit v3-Version zusammengeführt und sicherheitsgehärtet

### `systemd/botuser-sudoers`
- **Security:** `bash -c` Regel entfernt → Verweis auf `/usr/local/bin/drop-caches.sh` Script
- **Security:** `tar *` Wildcard eingeschränkt auf `tar czf /home/botuser/Discord_Bots/backups/*`
- **Security:** `renice` eingeschränkt auf `renice -10 -p *`
- **UFW Regeln:** Neue Einträge für Player-IP-Blocking hinzugefügt

### `systemd/bot-watchdog.service`
- **Vervollständigt:** `[Install]` Section, `WorkingDirectory`, `StandardOutput/Error`, `SyslogIdentifier` und Dependency auf Bot-Services hinzugefügt

### `scripts/drop-caches.sh`
- **Neu erstellt:** Dediziertes Script für Cache-Clearing (ersetzt unsichere `bash -c` Ausführung in sudoers)

---

## Offene Punkte (manuell auf dem Server zu erledigen)

1. **API Token generieren:** Im Satisfactory Admin Panel den API Token erstellen und in `config/.env` eintragen (Zeile `API_TOKEN=`)
2. **Discord Tokens:** `DISCORD_TOKEN_MANAGER` und `DISCORD_TOKEN_WATCHDOG` auf dem Server korrekt setzen
3. **drop-caches.sh installieren:** `sudo cp scripts/drop-caches.sh /usr/local/bin/drop-caches.sh && sudo chmod 755 /usr/local/bin/drop-caches.sh`
4. **sudoers aktualisieren:** `sudo cp systemd/botuser-sudoers /etc/sudoers.d/botuser && sudo chmod 440 /etc/sudoers.d/botuser && sudo visudo -c -f /etc/sudoers.d/botuser`
5. **v3_optimizer_update/ Ordner:** Kann auf dem Server bestehen bleiben als Referenz. Alle relevanten Dateien wurden integriert.
6. **SMTP Passwort:** In `config/.env` Zeile `SMTP_PASS=` auf dem Server setzen
7. ~~**Type Hints vervollständigen**~~ ✅ ERLEDIGT — 36 Dateien vollständig annotiert
8. ~~**Bare Exceptions reduzieren**~~ ✅ ERLEDIGT — 18 Stellen in 12 Dateien auf spezifische Exceptions umgestellt
