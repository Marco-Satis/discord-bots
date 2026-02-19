# Offene Review-Befunde — Marcos Entscheidung erforderlich

> **Datum:** 19. Februar 2026
> **Status:** Warten auf Marcos Freigabe

---

## B1: Minecraft-Cog — Doppelte Ausfuehrung von Stop/Restart

**Datei:** `cogs/minecraft_cog.py`, Zeilen 193–202 und 258–267

**Problem:** Der `RestartTimer` ruft `on_complete` intern auf (nach Countdown-Ende),
danach ruft der Cog `perform_stop()`/`perform_restart()` **nochmals** auf.
Der Server wird dadurch doppelt gestoppt/neu gestartet.

**Moegliche Loesungen:**
1. Den zweiten Aufruf nach dem Timer entfernen (Timer erledigt es bereits)
2. Das `on_complete`-Callback entfernen und nur den manuellen Aufruf behalten

**Hinweis:** Minecraft-Modul ist Phase 14 (Platzhalter) — eventuell erst spaeter relevant.

---

## B2: Scheduler — Lautloses Verschlucken von save_game()-Fehlern

**Datei:** `cogs/scheduler_cog.py`, Zeilen 195–196, 293–294, 433–434

**Problem:** Vor Auto-Backup, Daily Restart und Auto-Update wird `save_game()` aufgerufen.
Bei Fehler wird `except Exception: pass` verwendet — das Backup/Restart laeuft dann
mit moeglicherweise veraltetem Savegame-Stand weiter, ohne Warnung.

**Moegliche Loesungen:**
1. Fehler loggen aber weitermachen (`logger.warning(...)`)
2. Bei Save-Fehler das Backup/Restart abbrechen
3. Fehler loggen UND Admin-Channel benachrichtigen

---

## B3: Monitor-Bot — Lautloses Verschlucken bei Status-Load

**Datei:** `bots/monitor_bot.py`, Zeilen 285–286, 633–634

**Problem:**
- `_load_status_message_id()`: Fehler beim Laden der gespeicherten Status-Message-ID
  werden komplett verschluckt → Bot erstellt neue Embed statt alte zu bearbeiten
- `_init_log_position()`: Fehler beim Initialisieren der Log-Position verschluckt
  → Kann dazu fuehren dass Spieler-Events verpasst werden

**Moegliche Loesung:** `except Exception: pass` → `except Exception as e: logger.debug(...)`

---

## B4: Race Condition — Shared Mutable State

**Dateien:** `bots/monitor_bot.py`, `modules/monitoring/crash_replay.py`

**Problem:**
- `_log_last_pos`/`_log_last_size` (monitor_bot.py, Zeilen 622–623): Gelesen/geschrieben
  von `_poll_player_events()` (alle 10s) und potenziell von CrashReplay
- `_status_message_id` (monitor_bot.py): Geschrieben von Status-Update-Task
  und Command-Handler gleichzeitig
- `CrashReplay._last_pos/_last_size`: Gelesen/geschrieben von `update_buffer()` und `capture()`

**Risiko:** Gering (asyncio ist kooperativ), aber real bei bestimmten Task-Schedulings.

**Moegliche Loesung:** `asyncio.Lock()` fuer kritische Abschnitte einsetzen.
Dies waere eine Architektur-Aenderung.

---

## B5: Bot-Startup — get_event_loop() nach bot.run()

**Dateien:** `bots/gameserver_bot.py` (Zeilen 307, 314), `bots/monitor_bot.py` (Zeilen 1271, 1277)

**Problem:** Nach `bot.run()` (das den Event-Loop schliesst) wird `asyncio.get_event_loop()`
aufgerufen um `shutdown()` auszufuehren. Der Loop ist zu diesem Zeitpunkt bereits beendet.

**Hinweis:** `get_running_loop()` wuerde hier ebenfalls fehlschlagen.
Die korrekte Loesung waere `asyncio.run(shutdown())` oder `asyncio.new_event_loop()`.
Da dies den Startup-Ablauf betrifft, sollte Marco entscheiden.
