# Session-Stand: BMC5-Migration + Player-Detection Fix

> **Datum:** 12.–13. Maerz 2026
> **Status:** ABGESCHLOSSEN
> **Naechste Session:** Dokumentations-Aufgaben aus CLAUDE.md (Aufgaben 1-8)

---

## Was wurde gemacht

### 1. Umfassender Code-Review (164 Dateien)
Alle modifizierten Python-Dateien wurden vor dem Deployment reviewed. Dabei wurden 5 Bugs gefunden und behoben:

- **`cogs/satisfactory_cog.py`** — Blueprint-Delete-Befehle nutzten `delete_by_name()` ohne Berechtigungspruefung statt `delete()` mit User-ID + Admin-Check. Betroffen: Einzelloeschung (~Zeile 1050) und Bulk-Delete in `BlueprintDeleteConfirmView` (~Zeile 1641).
- **`cogs/scheduler_cog.py`** — `_perform_rollback()` fehlte Health-Suppress. Konnte zu Auto-Restart waehrend Rollback fuehren. Fix: `har.suppress("sat", "main", duration_seconds=900)` am Anfang der Methode.
- **`modules/minecraft/chat_bridge.py`** — Bracket-Escaping-Bug bei `[Intentional Game Design]`, doppelte DEATH_KEYWORDS, und `translate_mob_names()` ohne Word-Boundaries.

### 2. Deployment aller Aenderungen
Per SCP (Port 4422) auf den Produktionsserver deployed. Alle 3 Bots (gameserver-bot, monitor-bot, admin-bot) neu gestartet und verifiziert.

### 3. BMC3 → BMC5 Migration (NeoForge 1.21.1)
Kompletter Serverwechsel von Better MC 3 (Forge) auf Better MC 5 (NeoForge 1.21.1):

**Server-Setup:**
- NeoForge 21.1.217 installiert (nicht 21.1.172 — zu alt fuer BMC5-Mods)
- Pfad: `/home/minecraft/bmc5/` (alt: `/home/minecraft/bettermc/`)
- BMC5 Server Pack v47 hochgeladen und entpackt
- `server.properties` komplett neu geschrieben (Modpack ueberschreibt Defaults)
- `user_jvm_args.txt`: Xms4G, Xmx12G, G1GC-Tuning

**systemd Service (`minecraft-bmc.service`):**
```ini
Description=Minecraft Better MC 5 Server (NeoForge 1.21.1)
WorkingDirectory=/home/minecraft/bmc5
ExecStart=/home/minecraft/bmc5/run.sh nogui
ExecStop=/usr/bin/rcon-cli -a 127.0.0.1:25575 -p [PASSWORT] stop
MemoryMax=14G
```
- Wichtig: NeoForge nutzt `run.sh`, nicht direkt eine JAR-Datei
- rcon-cli v0.10.3 nutzt `-a host:port -p passwort` (nicht `--host`/`--port`)

**ENV-Aenderungen (config/.env):**
- `MC_BMC_PATH=/home/minecraft/bmc5`
- `MC_BMC_WORLD_PATH=/home/minecraft/bmc5/world`
- `MC_BMC_LOG_PATH=/home/minecraft/bmc5/logs/latest.log`
- `MC_BMC_BACKUP_PATH=/home/minecraft/bmc5_backups`
- `MC_BMC_SERVICE=minecraft-bmc.service` (fehlte vorher komplett)

**Ports (unveraendert):**
| Port | Service |
|------|---------|
| 25566 | BMC5 Game (TCP) |
| 25575 | BMC5 RCON (TCP, nur localhost) |

### 4. Chat-Bridge NeoForge-Kompatibilitaet
NeoForge nutzt ein anderes Log-Format als Vanilla:
```
Vanilla:   [21:00:50] [Server thread/INFO]: Spieler joined the game
NeoForge:  [12Maerz2026 21:00:50.857] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: Spieler joined the game
```

**Fix in `modules/minecraft/chat_bridge.py`:**
```python
_TS = r'\[[^\]]+\]'  # Zeitstempel (beliebig)
_TH = r'\[Server thread/INFO\](?:\s+\[[^\]]*\])*:\s+'  # Thread + optionale Logger-Tags
```
Alle 6 Regex-Patterns (CHAT_RE, JOIN_RE, LEAVE_RE, ADVANCEMENT_RE, DEATH_RE, SERVER_MSG_RE) verwenden jetzt `_TS` + `_TH` und matchen beide Formate.

Zusaetzlich: `translate_mob_names()` nutzt jetzt `re.sub()` mit `\b` Word-Boundaries und sortiert nach Namenlaenge (laengste zuerst) um Teilwort-Matches zu vermeiden.

### 5. Player-Detection Fix (Root Cause)
**Symptom:** Chat-Bridge zeigte keine Join/Leave-Events fuer BMC5.
**Vermutung:** Regex-Problem (NeoForge-Format). Regex wurde gefixt, funktionierte im Test — aber im laufenden Bot immer noch nichts.

**Root Cause:** Die **systemd Service-Datei** `monitor-bot.service` enthielt noch:
```ini
ReadWritePaths=/home/minecraft/bettermc
```
Da `/home/minecraft/bettermc` nach der BMC5-Migration nicht mehr existiert, konnte systemd den Mount-Namespace nicht aufsetzen → **der Monitor-Bot konnte gar nicht starten** und crash-loopte alle 15 Sekunden.

**Fix:**
```bash
sudo sed -i 's|/home/minecraft/bettermc|/home/minecraft/bmc5|g' /etc/systemd/system/monitor-bot.service
sudo systemctl daemon-reload && sudo systemctl restart monitor-bot
```

Nach dem Fix: Bridge pollt beide Server, Join/Leave/Chat-Events werden korrekt erkannt.

### 6. RAM-Konfiguration
Recherche ergab: Satisfactory Megabase braucht 14-16 GB, BMC5 mit 200+ Mods braucht 10-12 GB.

**Aktuelle Zuweisung:**
- Satisfactory: Feste Zuweisung (Hauptserver, nicht dynamisch)
- BMC5: Xms4G / Xmx12G (dynamisch via G1GC)
- Vanilla: Platzhalter, minimal

---

## Aktuelle Server-Konfiguration

| Service | Status | Port | Pfad |
|---------|--------|------|------|
| Satisfactory | Aktiv | 7777, 15777 | /home/satisfactory/ |
| MC BMC5 (NeoForge 1.21.1) | Aktiv | 25566, 25575 (RCON) | /home/minecraft/bmc5/ |
| MC Vanilla | Offline | 25565, 25576 (RCON) | /home/minecraft/vanilla/ |
| GameServer Bot | Aktiv | — | /home/botuser/Discord_Bots/ |
| Monitor Bot | Aktiv | — | /home/botuser/Discord_Bots/ |
| Admin Bot | Aktiv | — | /home/botuser/Discord_Bots/ |
| Web-Dashboard | Aktiv | 8080 (intern), 443 (extern) | /home/botuser/Discord_Bots/web/ |

---

## Offene Punkte

### Prioritaet 1
- **CLAUDE.md Aufgaben 1-8:** Dokumentations- und Cleanup-Aufgaben aus dem Post-Review. Noch nicht begonnen. Aufgabe 1 (CHANGELOG korrigieren) ist am wichtigsten.
- **Alte BMC3-Dateien loeschen:** `sudo rm -rf /home/minecraft/bettermc` — erst wenn BMC5 stabil laeuft.
- **Doppelte ENV-Zeile:** `MC_BMC_LOG_PATH` war doppelt → Zeile 81 wurde geloescht.

### Prioritaet 2
- **SAT CPU/RAM zeigt 0:** psutil AccessDenied-Problem (aus frueherer Session).
- **MC Vanilla Server offline:** Entweder starten oder aus Monitoring ausschliessen.
- **Unbekannte Ports:** 8081, 8888, 9090 sind offen — pruefen was dort laeuft.
- **RCON BMC sporadische Fehler:** Timeout in `modules/minecraft/rcon.py` eventuell erhoehen.

### Prioritaet 3
- **Spieler-Online-Chart:** Nicht verifiziert ob korrekt.
- **Leveling-System Dashboard:** Verbesserungen ausstehend.

---

## Gelernte Lektionen

1. **systemd ReadWritePaths/BindPaths pruefen bei Pfad-Migrationen!** Der Monitor-Bot crash-loopte wegen eines veralteten Pfads in der Service-Datei. Die Fehlermeldung ("Failed to set up mount namespacing") war nicht offensichtlich als Pfad-Problem erkennbar.
2. **NeoForge Log-Format** unterscheidet sich von Vanilla: Langformat-Zeitstempel + zusaetzlicher Logger-Tag in eckigen Klammern.
3. **rcon-cli v0.10.3** nutzt `-a host:port` statt `--host`/`--port`.
4. **NeoForge nutzt `run.sh`**, nicht direkt eine JAR. Der Installer generiert `run.sh` und `run.bat`.
5. **Modpack-ZIPs ueberschreiben `server.properties`** — nach Entpacken IMMER pruefen und korrigieren (RCON, Ports, etc.).
