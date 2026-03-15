# Discord Bot System — Offene Punkte

> **Stand:** 15. März 2026
> **Referenz-Spezifikation:** `docs/FEATURE_PLAN_AUTO_UPDATE.md` (v1.6)
> **Code-Review:** `docs/CODE_REVIEW_AUTO_UPDATE.md`

---

## A0. Bug-Fixes Kern-Module (VOR Integration!)

Aus dem Code-Review identifizierte Bugs — müssen vor I1-I9 gefixt werden.
Details + Fix-Vorschläge: `docs/CODE_REVIEW_AUTO_UPDATE.md`

### BUG-1: mc_countdown.py — 30s-Banner Race Condition
**Zeile 198:** `asyncio.get_event_loop().call_later()` feuert auch nach Timer-Abbruch.
**Fix:** `asyncio.create_task()` mit Cancel-Support verwenden.

### BUG-2: file_manager.py — Doppelte Hash-Berechnung
**Zeile 166-167:** `_verify_hash()` liest 584 MB Datei nochmal, obwohl Hash bereits beim Download berechnet wurde.
**Fix:** Streaming-Hash aus Metadata vergleichen statt Datei nochmal zu lesen.

### BUG-3: neoforge_updater.py — Installer komplett in RAM geladen
**Zeile 184:** `resp.read()` lädt 100+ MB Installer komplett in Speicher.
**Fix:** Streaming-Download mit `iter_chunked()`.

### BUG-4: update_manager.py — Update auf laufendem Server möglich
**Zeile 524:** Wenn Server nach 30s nicht stoppt, wird nur gewarnt, Update läuft trotzdem weiter.
**Fix:** Harten Stop als Fallback + Abbruch wenn Server nicht stoppt.

### BUG-5: update_manager.py — Kein RCON `stop` vor systemctl
**Zeile 513:** Direkter systemctl-Stop statt graceful RCON `stop`.
**Fix:** RCON `stop` zuerst, systemctl als Fallback (Feature-Plan §4 Phase 3).

### BUG-6: update_manager.py — NeoForge im falschen Ordner installiert
**Zeile 262:** NeoForge-Installer läuft auf `server_path` (alt) statt `extract_dir` (neu), VOR dem Atomic Swap.
**Fix:** NeoForge NACH dem Atomic Swap auf dem neuen Server-Pfad installieren.

### BUG-7: update_manager.py — Crash-Recovery ohne Rollback
**Zeile 440-455:** Bei Crash-Recovery + 3 fehlgeschlagenen Starts wird KEIN Rollback gemacht.
**Fix:** Rollback-Pfad aus DB-Eintrag lesen und `_perform_rollback()` aufrufen.

### RISK-5: HAR-Suppress zu kurz
**Zeile 483:** `(countdown * 60) + 900` = max 25 Min. Update kann 45+ Min dauern.
**Fix:** HAR-Suppress bei jedem Phasenwechsel um 15 Min verlängern.

**Empfohlene Reihenfolge:** BUG-6 → BUG-7 → BUG-4 → BUG-5 → BUG-1 → BUG-2 → BUG-3 → RISK-5

### Nach A0: Tests
```bash
python tests/test_imports.py
```

---

## A. Auto-Update Integration (Hauptaufgabe)

Zuerst: **alle A0-Bugs fixen!**

### I1: monitor_bot.py — UpdateManager einbinden
**Feature-Plan:** §2, §4 Phase 0, §7
**Datei:** `bots/monitor_bot.py`
**Deploy-Gruppe 1:** MUSS zusammen mit I4 deployed werden

Was zu tun ist:
- `UpdateManager` importieren und pro MC-Server instanziieren (nach mc_servers Loop)
- `ModpackUpdater.from_env()` aufrufen (ersetzt alten Konstruktor, Zeile 559-564)
- `check_and_resume()` in `on_ready` aufrufen (Crash-Recovery)
- Chat-Bridge: Referenzen auf UpdateManager + MinecraftServer übergeben
- Voice-Channel von "X Online" auf "X/Y Online" — alle bestehenden Caller anpassen
- UpdateManager an `bot.mc_update_managers` hängen (für Scheduler + Discord-Commands)
- **Caller-Check VOR Implementierung:** `grep -rn "get_player_count" modules/ bots/ cogs/ web/`

### I2: scheduler_cog.py — Update-Zeitplan
**Feature-Plan:** §3, §11, §16
**Datei:** `cogs/scheduler_cog.py`

Was zu tun ist:
- Neuer Task: MC Modpack-Check um 12:00 und 00:00
- **12:00-Check:** Bei neuer Version → sofort Auto-Update mit 10-Min-Countdown
- **00:00-Check:** Bei neuer Version → Flag in DB: `modpack_updates.status='scheduled', update_type='auto_daily'`
- **04:00 Daily-Restart:** Prüfe `modpack_updates` auf `status='scheduled'`. Falls ja → `UpdateManager.run_update()` (setzt `status='in_progress'` intern). Falls nein → normaler Restart.
- SAT-Updater in gleichen Zeitplan (12:00/00:00 SteamCMD Build-ID Check)
- Retention-Cleanup: Rollback-Ordner + Server-Pack-ZIPs (2 Versionen)
- DB-Cleanup: Fehlgeschlagene Updates >90 Tage + scheduled-Einträge >7 Tage (F63-Cleanup erweitern)

### I3: chat_bridge.py — In-Game-Befehle + Bugfixes
**Feature-Plan:** §6
**Datei:** `modules/minecraft/chat_bridge.py`

Was zu tun ist:
- `COMMAND_RE` Regex, `_handle_ingame_command()` Dispatcher
- 8 Befehle: `!status`, `!version`, `!players`, `!tps`, `!cancel`, `!restart`, `!backup`, `!help`
- OP-Prüfung via `ops.json`, `tellraw`-Antworten via RCON
- Neue Abhängigkeiten: `mc_server` + `update_manager` im Konstruktor
- **Gleichzeitig fixen:** Bracket-Bug Zeile 83/104, 2 doppelte Death-Keywords

### I4: server.py — Spielererkennung-Fix
**Feature-Plan:** §7 | **Deploy-Gruppe 1:** zusammen mit I1!

Breaking Change: `get_player_count()` → `Tuple[int, int]` statt `int`.
- Regex für 3 Formate (Vanilla, NeoForge, Paper)
- `_get_max_players_fallback()` aus `server.properties`

### I5: discord_notifier.py — DM an Owner
**Feature-Plan:** §15
- `send_dm_to_owner(title, description, level, fields)`
- `BOT_OWNER_ID` aus ENV, Fallback bei `discord.Forbidden`

### I6: restart_timer.py — Methode extrahieren
**Feature-Plan:** §2 | **Muss fertig sein BEVOR I1 deployed wird**
- `_send_ingame_warning()` als überschreibbare Methode extrahieren

### I7: Discord-Commands MC
**Feature-Plan:** §8 | Neues Cog `cogs/update_cog.py` bevorzugt (monitor_cog.py >800 Zeilen)
- `/mc modpack status/update/cancel/rollback/history/check`

### I8: Discord-Commands SAT
**Feature-Plan:** §8, §16
- `/sat update` + `/sat update cancel`

### I9: ENV-Dokumentation
**Feature-Plan:** §10 | Datei: `config/.env.example`

---

## Timeout-Werte

| Server-Typ | Timeout | Grund |
|------------|---------|-------|
| MC (NeoForge) | 120s (4s Polling, 30 Versuche) | 200+ Mods |
| SAT (SteamCMD) | 90s (`_safe_start()`) | Schnellerer Start |

## Deploy-Gruppen

| Gruppe | Aufgaben | Rollback |
|--------|----------|----------|
| Gruppe 1 | I4 + I1 + Caller aus grep | ALLE Dateien zurückrollen |
| Abhängigkeit | I6 vor I1 | I6 allein kein negativer Effekt |
| Einzeln | I5, I3, I2, I7, I8, I9 | Einzeldatei reicht |

DB-Migration: erst monitor-bot, 10s warten, dann Rest.

---

## B. Server-Setup (einmalig, vor erstem Test)

### B0: SSH-Key prüfen (VOR allem anderen!)
```bash
ssh -p 4422 -o BatchMode=yes -o ConnectTimeout=5 marco@203.0.113.10 "echo SSH OK"
```
Falls "Permission denied": Marco muss SSH-Key einrichten.

### B1: Staging-Verzeichnis
```bash
sudo mkdir -p /home/minecraft/.update_staging
sudo chown botuser:minecraft /home/minecraft/.update_staging
sudo chmod 775 /home/minecraft/.update_staging
```

### B2: Sudoers (Feature-Plan §17)
```bash
cat > /etc/sudoers.d/botuser-updates << 'EOF'
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/.update_staging/* /home/minecraft/*
botuser ALL=(minecraft) NOPASSWD: /bin/cp -r /home/minecraft/.update_staging/* /home/minecraft/*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/mods /home/minecraft/bmc5/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/config /home/minecraft/bmc5/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/mods_rollback_* /home/minecraft/bmc5/mods
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/config_rollback_* /home/minecraft/bmc5/config
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/mods /home/minecraft/vanilla/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/config /home/minecraft/vanilla/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/mods_rollback_* /home/minecraft/vanilla/mods
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/config_rollback_* /home/minecraft/vanilla/config
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/bmc5/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/bmc5/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/vanilla/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/vanilla/config_rollback_*
botuser ALL=(root) NOPASSWD: /bin/chown -R minecraft\:minecraft /home/minecraft/bmc5/*
botuser ALL=(root) NOPASSWD: /bin/chown -R minecraft\:minecraft /home/minecraft/vanilla/*
EOF
chmod 440 /etc/sudoers.d/botuser-updates
visudo -cf /etc/sudoers.d/botuser-updates
```

### B3: ENV-Variablen (Werte bei Marco erfragen)
CURSEFORGE_API_KEY, MC_BMC_CURSEFORGE_PROJECT_ID=462042, MC_BMC_CURSEFORGE_FILE_ID=7449464, MC_BMC_MODPACK_VERSION=v47, BOT_OWNER_ID=1000000000000000001

### B4: Berechtigungen testen
```bash
sudo -u botuser touch /home/minecraft/.update_staging/test.txt && sudo -u botuser rm /home/minecraft/.update_staging/test.txt && echo "Staging OK"
sudo -u botuser mkdir -p /home/minecraft/.update_staging/test_rb && sudo -u botuser sudo -u minecraft mv /home/minecraft/.update_staging/test_rb /home/minecraft/bmc5/mods_rollback_test && sudo -u botuser sudo -u minecraft mv /home/minecraft/bmc5/mods_rollback_test /home/minecraft/.update_staging/ && sudo -u botuser rm -rf /home/minecraft/.update_staging/test_rb && echo "Rollback OK"
```

---

## C. Deployment

SCP-Liste vor Deployment prüfen. Bugfixes 15. März sind lokal fertig, NICHT deployed.

### Restart (Reihenfolge!)
```bash
sudo systemctl restart monitor-bot   # DB-Migration v4 zuerst
sleep 10
sudo systemctl restart gameserver-bot admin-bot web-dashboard
```

### Verifikation
1. Logs: `journalctl -u monitor-bot --since '2 min ago' | grep -iE 'error|exception|traceback'`
2. DB: `sqlite3 data/botdata.db 'PRAGMA user_version'` → 4
3. Tabellen: modpack_updates, server_versions vorhanden
4. CurseForge API erreichbar
5. Gameserver-Bot online (infinity-Fix)
6. HAR suppress funktioniert

---

## D. Bekannte Bugs (niedrigere Priorität)

| Bug | Prio | Beschreibung |
|-----|------|-------------|
| SAT CPU/RAM zeigt 0 | 2 | psutil AccessDenied, /proc-Fallback fehlerhaft |
| Spieler-Online-Chart | 3 | Nach StatsCollector-Fix nicht verifiziert |
| RCON BMC sporadisch | 3 | Retry-Logik vorhanden, beobachten |
| MC Vanilla offline | 3 | Entscheidung offen |
| Unbekannte Ports | 3 | 8081, 8888, 9090 |

Chat-Bridge Bracket-Bug + Duplikate → in I3 integriert.

## E. Server-Cleanup (minor)

Pycache (128 .pyc), Temp-Dateien, WEB_ADMIN_PASS_HASH, .env-Scanner blockieren, VERSION auf 4.1.0 nach Deployment

---

## Reihenfolge

**Phase 1 — A0 Bug-Fixes:** BUG-6→7→4→5→1→2→3→RISK-5 → Tests
**Phase 2 — Integration:** I6 → I4 → I5 → I1 (Gruppe 1 mit I4) → I3 → I2 → I7+I8 → I9
**Phase 3 — Deploy:** B0-B4 → C (DB zuerst, dann Rest)
