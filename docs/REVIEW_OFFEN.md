# Offene Review-Befunde — Marcos Entscheidung erforderlich

> **Datum:** 19. Februar 2026
> **Status:** Alle Befunde behoben (B1-B5)

---

## ~~B1: Minecraft-Cog — Doppelte Ausfuehrung von Stop/Restart~~ BEHOBEN

Loesung 1 umgesetzt in Phase 14f: Zweiter Aufruf nach Timer entfernt.
`on_complete` fuehrt `perform_stop()`/`perform_restart()` aus, danach nur noch
`TimerResult.CANCELLED` Check. Kein doppelter Aufruf mehr.

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
