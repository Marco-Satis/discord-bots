# Offene Review-Befunde — Marcos Entscheidung erforderlich

> **Datum:** 19. Februar 2026
> **Status:** Aktualisiert

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

## ~~B2: Scheduler — save_game() Fehler~~ BEHOBEN

Loesung 1 umgesetzt: `except Exception: pass` → `logger.warning(...)` an 3 Stellen.

## ~~B3: Monitor-Bot — Lautloses Verschlucken~~ BEHOBEN

`except Exception: pass` → `except Exception as e: logger.debug(...)` an 2 Stellen.

## ~~B4: Race Condition — Shared Mutable State~~ BEHOBEN

- `_log_lock` fuer `_poll_player_events()` in monitor_bot.py
- `_status_lock` fuer Status-Embed Send/Edit in monitor_bot.py
- `self._lock` fuer `update_buffer()` und `capture()` in crash_replay.py

## ~~B5: Bot-Startup — get_event_loop() nach bot.run()~~ BEHOBEN

`asyncio.get_event_loop().run_until_complete(shutdown())` → `asyncio.run(shutdown())`
in beiden Bot-Dateien.
