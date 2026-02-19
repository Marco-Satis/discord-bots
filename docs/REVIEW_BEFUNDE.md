# Code-Review Befunde — Phase 2

> **Datum:** 19. Februar 2026
> **Reviewer:** Claude Code (autonom)
> **Umfang:** Alle 56 Python-Dateien (ohne modules/minecraft/ Platzhalter)

---

## Zusammenfassung der durchgefuehrten Fixes

### Commit 1: `[Bug-Fix] /clear Datums-Validierung` (Phase 1)
- `cogs/general_cog.py`: `followup.send()` → `edit_original_response()` bei Datums-Validierungsfehlern

### Commit 2: `[Review] monitor_bot.py`
- Ungenutzte Imports entfernt: `os`, `time`, `psutil`, `app_commands`
- `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (Zeile 669)
- Inline `from datetime import timedelta` → top-level

### Commit 3: `[Review] Cogs`
- `general_cog.py`: Inline `import time` → top-level
- `mod_cog.py`: Ungenutztes `format_timestamp` entfernt
- `scheduler_cog.py`: Ungenutzte `dt_time`, `Path`, `is_owner` entfernt

### Commit 4: `[Review] Satisfactory-Module`
- `savegame_analyzer.py`: Ungenutztes `date` entfernt
- `savegame_stats.py`: Ungenutztes `os` entfernt
- `blueprint_manager.py`: Inline `import io` → top-level
- `save_header.py`: `IOError` → `OSError`
- `settings_backup.py`: Redundantes `IOError` aus Exception-Tuple entfernt

### Commit 5: `[Review] Monitoring-Module`
- `player_tracker.py`: `IOError` → `OSError` (2 Stellen)
- `auto_cleanup.py`: Inline `import psutil` → top-level
- `selftest.py`: Inline `import psutil` → top-level
- `savegame_protection.py`: `IOError` → `OSError` (3 Stellen)
- `stats_tracker.py`: `IOError` → `OSError` (2 Stellen)

### Commit 6: `[Review] Backup, Notifications, Utils`
- `backup_manager.py`: Ungenutztes `os`, `IOError` → `OSError`, `get_event_loop` → `get_running_loop`
- `onedrive_backup.py`: Inline `import json` → top-level, ungenutztes `re` entfernt
- `email_notifier.py`: `get_event_loop` → `get_running_loop`
- `command_logger.py`: `IOError` → `OSError` (2 Stellen)
- `config_validator.py`: Ungenutztes `os`, `IOError` → `OSError` (2 Stellen)
- `permissions.py`: Ungenutztes `functools` entfernt
- `formatting.py`: Ungenutztes `timedelta` entfernt

---

## Offene Befunde (Phase 3)

### A. Autonom fixbar (verhaltens-neutral)

| # | Datei | Problem | Zeilen |
|---|-------|---------|--------|
| A1 | `modules/maintenance.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 330 |
| A2 | `modules/monitoring/auto_cleanup.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 139, 163 |
| A3 | `modules/monitoring/crash_replay.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 112, 159 |
| A4 | `modules/monitoring/login_audit.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 84 |
| A5 | `modules/monitoring/savegame_protection.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 242, 254 |
| A6 | `modules/satisfactory/savegame_analyzer.py` | `asyncio.get_event_loop()` → `get_running_loop()` | 613 |
| A7 | `modules/monitoring/auto_cleanup.py` | Toter Code: `for *.log: pass`-Schleife | 119–122 |
| A8 | `cogs/minecraft_cog.py` | Nackte `except:` ohne Exception-Typ | 190, 206, 255, 271 |

### B. Marcos Entscheidung erforderlich → siehe `docs/REVIEW_OFFEN.md`

| # | Datei | Problem |
|---|-------|---------|
| B1 | `cogs/minecraft_cog.py` | `perform_stop()`/`perform_restart()` werden doppelt ausgefuehrt (Timer + manuell) |
| B2 | `cogs/scheduler_cog.py` | `save_game()` Fehler vor Backup/Restart werden lautlos verschluckt |
| B3 | `bots/monitor_bot.py` | Status-Message-ID und Log-Position Fehler lautlos verschluckt |
| B4 | `bots/monitor_bot.py` + `crash_replay.py` | Race Condition: Shared State ohne Locks |
| B5 | `bots/gameserver_bot.py`, `bots/monitor_bot.py` | `get_event_loop()` nach `bot.run()` — Loop bereits beendet |
