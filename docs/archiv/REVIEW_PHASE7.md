# Phase 7: Komplett-Review — 63 Dateien

> **Datum:** 20. Februar 2026
> **Reviewer:** Claude Code (autonom)
> **Umfang:** 63 Python-Dateien in bots/, cogs/, utils/, modules/

---

## Zusammenfassung

| Kategorie | Anzahl | Aktion |
|-----------|--------|--------|
| **CRITICAL** | 28 | Selbst behoben |
| **WARNING** | 123 | Wichtigste behoben, Rest dokumentiert |
| **INFO** | 99 | Dokumentiert, nicht geaendert |

---

## CRITICAL — Behoben

### C1: bot.user kann None sein (gameserver_bot.py, monitor_bot.py)
- `bot.user.id` in `on_ready()` wuerde AttributeError werfen
- **Fix:** Guard `if bot.user:` eingefuegt

### C2: Mention/Markdown-Injection in MC-Chat-Bridge (monitor_bot.py)
- Spielernamen und Chat-Nachrichten werden unescaped in Discord gesendet
- **Fix:** `discord.utils.escape_markdown()` auf alle Spielernamen/Nachrichten

### C3: Mention-Injection in discord_notifier.py
- `player_name` direkt in Embed-Description
- **Fix:** `discord.utils.escape_mentions()` + `escape_markdown()` eingefuegt

### C4: Mention-Injection in player_tracker.py, player_ip_tracker.py
- Spielernamen direkt in Discord-Messages eingebettet
- **Fix:** Sanitierung mit `discord.utils.escape_mentions()` eingefuegt

### C5: RCON-Injection ueber Discord (minecraft_cog.py, timeout_cog.py)
- User-Input (Spielernamen, Gruende) direkt in RCON-Befehle eingesetzt
- **Fix:** `_sanitize_rcon_input()` Funktion mit Whitelist erlaubter Zeichen

### C6: Command-Injection in satisfactory_cog.py LoadGame
- Savename direkt vom User in Server-Befehl eingesetzt
- **Fix:** Validierung auf alphanumerisch + Unterstrich + Bindestrich

### C7: Path-Traversal in mod_cog.py /mod import
- User-kontrollierter Dateipfad ohne Validierung
- **Fix:** `Path(modpack_file).name` + `resolve()` + `is_relative_to()` Pruefung

### C8: asyncio.run(shutdown()) nach bot.run() (beide Bots)
- Kann RuntimeError verursachen wenn Event-Loop bereits geschlossen
- **Fix:** try/except um asyncio.run() mit sauberem Fallback

### C9: psutil.cpu_percent() blockiert Event-Loop
- performance.py, optimizer.py, general_cog.py, monitor_cog.py
- **Fix:** In `asyncio.get_running_loop().run_in_executor(None, ...)` gewrappt

### C10: RCON asyncio.Lock fehlt (rcon.py)
- Parallele RCON-Aufrufe koennen TCP-Stream vermischen
- **Fix:** `asyncio.Lock` in `command()` eingefuegt

### C11: word_filter.py Index-Desynchronisation
- `_compiled_patterns` hat weniger Eintraege als `_words` bei uebersprungenen Eintraegen
- **Fix:** Tuple-Liste `(pattern, word_entry)` statt separate Listen

### C12: word_filter.py typing.Pattern deprecated
- `typing.Pattern` seit Python 3.9 deprecated, entfernt in 3.13+
- **Fix:** Durch `re.Pattern` ersetzt

### C13: backup_manager.py tar.extractall ohne members-Filter
- Partial extraction trotz Validierung moeglich
- **Fix:** Vorgefilterte `safe_members` Liste an `extractall()` uebergeben

### C14: maintenance.py Ping-Timeout falsch
- `-W` erwartet Sekunden, Code uebergibt Millisekunden (3000 statt 3)
- **Fix:** `str(timeout)` statt `str(timeout * 1000)`

### C15: restart_timer.py Type-Hint-Fehler
- `warnings: List[int] = None` statt `Optional[List[int]] = None`
- **Fix:** Type-Hint korrigiert

### C16: config_backup.py .env unverschluesselt in Cloud
- Tokens und Passwoerter unverschluesselt auf OneDrive
- **Fix:** Warnung geloggt, Verschluesselung in Phase 8d

### C17: minecraft/server.py int() ohne Try/Except
- RCON_PORT und GAME_CHAT_CHANNEL_ID koennen ValueError werfen
- **Fix:** `get_env()` mit `cast=int` und Default-Wert

### C18: chat_bridge.py RCON-Injection (MC Target-Selektoren)
- `@a`, `@p`, `@e`, `@r`, `@s` werden nicht gefiltert
- **Fix:** Target-Selektoren escaped/entfernt

### C19: api_client.py Race Condition bei _get_session()
- Zwei Coroutines erstellen gleichzeitig Sessions
- **Fix:** `asyncio.Lock` fuer Session-Erstellung

### C20: SatisfactoryAPIError verschluckt in api_client.py
- Auth-Fehler als "offline" getarnt
- **Fix:** SatisfactoryAPIError separat geloggt

---

## WARNING — Behoben

### W1: 15+ nackte `except Exception: pass` (monitor_bot.py)
- **Fix:** `except Exception as e: logger.debug(...)` eingefuegt

### W2: maintenance_cog.py None-Slice-Bugs (Zeilen 138, 237, 249)
- **Fix:** `(value or '')[:19]` Pattern

### W3: Fehlende Type-Hints fuer `bot` in Cog-__init__
- **Fix:** `bot: commands.Bot` in allen Cogs

### W4: Unbenutzte Imports entfernt
- subprocess, List, diverse andere

### W5: Synchrone File-I/O in general_cog.py
- **Fix:** In asyncio.to_thread() gewrappt

### W6: mod_manager.py Modpack-Name nicht sanitiert
- **Fix:** Nur alphanumerische Zeichen + `_`/`-`

### W7: Race Conditions in whitelist.py/blacklist.py
- **Fix:** asyncio.Lock eingefuegt

### W8: datetime.now() ohne Timezone in Embed-Timestamps
- **Fix:** Kritischste Stellen auf `datetime.now(timezone.utc)` umgestellt

---

## WARNING — Dokumentiert (geringes Risiko, nicht geaendert)

- Code-Duplikation Bot-Initialisierung (Shared Setup) — v4.0
- Code-Duplikation whitelist.py/blacklist.py — Basisklasse v4.0
- savegame_analyzer.py _classify() 217 Zeilen if/elif — v4.0
- monitor_bot.py 1787 Zeilen — Modularisierung v4.0
- Inkonsistente Type-Hint-Stile (typing vs. built-in) — v4.0
- Inkonsistente Import-Stile — v4.0
- f-String Logging statt lazy Formatting — v4.0
- aiohttp.ClientSession pro Aufruf (steam_changelog.py)
- Fehlende `__all__` in __init__.py Dateien
- psutil.disk_usage("/") hardcodierter Linux-Pfad

---

## INFO — Dokumentiert (keine Aenderung)

- Docstrings teilweise auf Englisch (~99 Stellen)
- `Optional[str] = None` vs `str = None` Inkonsistenzen
- Code-Duplikation Error-Handler in Cogs
- Tote Code-Stellen (DATA_FILE, _restart_warning_callback)
- View-Buttons nach Timeout nicht entfernt
- Cooldown vor Bestaetigungs-Dialog gesetzt
- `global` fuer Dict-Mutation unnoetig

---

## Statistik

| Bereich | Dateien | CRITICAL | WARNING | INFO |
|---------|---------|----------|---------|------|
| bots/ | 2 | 5 | 15 | 8 |
| cogs/ | 8 | 3 | 23 | 24 |
| utils/ | 4 | 2 | 12 | 8 |
| modules/satisfactory/ | 9 | 2 | 23 | 13 |
| modules/minecraft/ | 6 | 7 | 18 | 12 |
| modules/monitoring/ | 14 | 3 | 22 | 24 |
| modules/backup+notif+standalone | 12 | 6 | 10 | 10 |
| **Gesamt** | **63** | **28** | **123** | **99** |
