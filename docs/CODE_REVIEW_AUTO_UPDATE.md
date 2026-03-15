# Code-Review: Auto-Update Kern-Module — Bugs, Risiken, Optimierungen

> **Datum:** 15. März 2026
> **Geprüft:** update_manager.py, file_manager.py, mc_countdown.py, neoforge_updater.py, modpack_updater.py, migrations.py
> **Plus Zieldateien:** monitor_bot.py, chat_bridge.py, scheduler_cog.py, server.py, restart_timer.py, discord_notifier.py

---

## A. Bugs in Kern-Modulen (MUSS vor Deployment gefixt werden)

### BUG-1: mc_countdown.py — 30s-Banner Race Condition (Zeile 198)
```python
asyncio.get_event_loop().call_later(
    30,
    lambda: asyncio.ensure_future(
        self._rcon_title("SERVER NEUSTART", "dark_red", "in 30 Sekunden!", "red")
    ),
)
```
**Problem:**
1. `get_event_loop()` ist deprecated seit Python 3.10 — kann RuntimeWarning werfen
2. Wenn der Timer abgebrochen wird (`cancel()`), feuert die 30s-Warnung trotzdem (kein Cleanup)
3. Das Lambda erfasst `self` — wenn das Objekt bereits garbage-collected ist → Exception

**Fix:**
```python
# Stattdessen: Task mit Cancel-Support
self._30s_task = asyncio.create_task(self._delayed_30s_warning())

async def _delayed_30s_warning(self):
    try:
        await asyncio.sleep(30)
        if not self._cancelled:
            await self._rcon_title("SERVER NEUSTART", "dark_red", "in 30 Sekunden!", "red")
    except asyncio.CancelledError:
        pass
```
Und in `_send_cancel_message()`: `if self._30s_task: self._30s_task.cancel()`

**Schwere:** Mittel — führt zu verwirrender "30 Sekunden"-Warnung NACH Abbruch

---

### BUG-2: file_manager.py — Doppelte Hash-Berechnung (Zeile 166 vs. 234)
**Problem:** `download_file()` ruft `_download_once()` auf, welches den Hash **während** des Downloads berechnet (Streaming, Zeile 234-245). Danach ruft `download_file()` zusätzlich `_verify_hash()` auf (Zeile 167), die die **gesamte 584 MB Datei nochmal liest** um denselben Hash zu berechnen.

**Fix:** `_verify_hash()` sollte die bereits berechneten Hashes aus `metadata["sha1"]` und `metadata["md5"]` verwenden statt die Datei nochmal zu lesen:
```python
# In download_file(), statt:
hash_ok = await self._verify_hash(dest_path, expected_sha1, expected_md5)
# Besser:
if expected_sha1 and metadata["sha1"].lower() != expected_sha1.lower():
    hash_ok = False
elif expected_md5 and metadata["md5"].lower() != expected_md5.lower():
    hash_ok = False
else:
    hash_ok = True
```

**Schwere:** Niedrig — funktioniert, ist nur unnötig langsam (584 MB doppelt lesen)

---

### BUG-3: neoforge_updater.py — Installer in Speicher geladen (Zeile 184)
```python
content = await resp.read()  # Gesamter Installer in RAM
installer_jar.write_bytes(content)
```
**Problem:** NeoForge-Installer können 100+ MB groß sein. `resp.read()` lädt alles in den RAM.

**Fix:** Streaming-Download verwenden (wie in file_manager.py):
```python
with open(installer_jar, "wb") as f:
    async for chunk in resp.content.iter_chunked(8192):
        f.write(chunk)
```

**Schwere:** Niedrig — funktioniert bei genug RAM, aber nicht elegant

---

### BUG-4: update_manager.py — _stop_server() ohne systemctl-Fallback (Zeile 512-524)
**Aktueller Code:**
```python
success, msg = await self.mc_server.stop()  # ruft systemctl stop auf
for _ in range(15):  # Polling ob gestoppt
    await asyncio.sleep(2)
    if not await self.mc_server.is_running():
        return
logger.warning("Server nach 30s noch aktiv")
```
**Problem:** Wenn `mc_server.stop()` zwar `systemctl stop` aufruft aber der Prozess hängt, gibt es keinen Fallback (z.B. `systemctl kill`). Der Code loggt nur eine Warnung und fährt trotzdem mit dem Update fort — auf einem noch laufenden Server!

**Fix:** Harten Stop als Fallback + Abbruch wenn Server nicht stoppt:
```python
logger.warning("Server nach 30s noch aktiv — Force-Stop")
await self.mc_server._systemctl("kill")
await asyncio.sleep(5)
if await self.mc_server.is_running():
    raise FileManagerError("server_stop", "Server konnte nicht gestoppt werden")
```

**Schwere:** Hoch — Update auf laufendem Server = Datenverlust möglich

---

### BUG-5: update_manager.py — Kein RCON `stop` vor systemctl (Zeile 513)
Der Feature-Plan (Phase 3) sagt: erst RCON `stop` (graceful), dann systemctl als Fallback.
Der aktuelle Code ruft direkt `self.mc_server.stop()` auf, was `systemctl stop` macht.

**Fix:** RCON `stop` vor systemctl:
```python
try:
    await self.mc_server.rcon_command("stop")
    # Warten auf graceful shutdown
    for _ in range(15):
        await asyncio.sleep(2)
        if not await self.mc_server.is_running():
            return
except Exception:
    pass
# Fallback: systemctl stop
await self.mc_server.stop()
```

**Schwere:** Mittel — systemctl stop ist weniger graceful, Chunks könnten nicht gespeichert sein

---

### BUG-6: update_manager.py — NeoForge install() VOR Atomic Swap (Zeile 262)
```python
# Zeile 262: NeoForge wird auf dem AKTUELLEN Server installiert
nf_ok, nf_msg = await self._neoforge_updater.install(new_nf)
# ...
# Zeile 279: DANN wird der Atomic Swap gemacht
swap_meta = await self.file_manager.atomic_swap(...)
```
**Problem:** `install()` nutzt `self.server_path` (den aktuellen Server-Pfad) und führt den Installer dort aus. Aber die neuen Dateien liegen noch in `extract_dir` (Staging). Der Installer modifiziert also den **alten** Server-Ordner, nicht den neuen.

**Fix:** Entweder:
a) NeoForge-Installer auf `extract_dir` statt `server_path` ausführen, ODER
b) NeoForge-Installer NACH dem Atomic Swap auf dem neuen Server-Ordner ausführen

Option b) ist sicherer (Rollback ist dann einfacher):
```python
# ERST Atomic Swap
await self.file_manager.atomic_swap(...)
# DANN NeoForge auf dem neuen (jetzt aktiven) Pfad
self._neoforge_updater = NeoForgeUpdater(server_path)
nf_ok, nf_msg = await self._neoforge_updater.install(new_nf)
```

**Schwere:** HOCH — NeoForge wird im falschen Ordner installiert

---

### BUG-7: update_manager.py — Crash-Recovery macht kein Rollback (Zeile 440)
Die Crash-Recovery (`check_and_resume()`) versucht nur den Server zu starten. Wenn er nicht startet (3 Versuche überschritten), wird der Update-Log als "failed" markiert — aber es wird **kein Rollback** durchgeführt. Der defekte neue Code bleibt liegen.

**Fix:**
```python
elif attempts + 1 >= MAX_START_ATTEMPTS:
    # Rollback-Ordner finden
    rollback_path = row.get("rollback_path")
    if rollback_path and Path(rollback_path).exists():
        await self._perform_rollback(Path(rollback_path), self._get_server_path())
    await self._finalize_update_log(update_id, "rolled_back", ...)
```

**Schwere:** HOCH — defekter Server bleibt nach Crash-Recovery liegen

---

## B. Risiken bei Integration (I1-I9)

### RISK-1: I1 (monitor_bot.py) — UpdateManager braucht viele Abhängigkeiten
Der UpdateManager-Konstruktor braucht: `mc_server`, `modpack_updater`, `channel`, `db`, `har`, `notifier`, `backup_manager`, `onedrive_backup`. Diese werden an verschiedenen Stellen in monitor_bot.py erstellt. **Timing ist kritisch:** Alles muss initialisiert sein bevor `check_and_resume()` in `on_ready()` aufgerufen wird.

**Empfehlung:** UpdateManager erst in `on_ready()` erstellen (nicht im globalen Scope), nachdem alle Services initialisiert sind.

### RISK-2: I3 (chat_bridge.py) — RCON-Injection über In-Game-Befehle
Die Chat-Bridge sendet RCON-Antworten via `/tellraw`. Wenn ein Spielername oder Befehlsargument Sonderzeichen enthält (z.B. `"`, `}`, `\`), könnte das JSON-Format brechen oder unerwünschte Befehle injizieren.

**Empfehlung:** JSON-Escape für alle User-Inputs in tellraw:
```python
import json
safe_text = json.dumps(text)[1:-1]  # Entfernt äußere Anführungszeichen
```

### RISK-3: I2 (scheduler_cog.py) — 00:00-Check "scheduled" Status
Der neue `scheduled`-Status muss im Scheduler korrekt gesetzt und beim 04:00-Restart abgefragt werden. Aber: was wenn der Bot zwischen 00:00 und 04:00 neustartet? Die Crash-Recovery sucht nur nach `in_progress`, nicht `scheduled`.

**Empfehlung:** `check_and_resume()` auch `scheduled`-Einträge prüfen, ODER den 04:00-Task unabhängig vom Status machen (er prüft einfach nochmal ob ein Update verfügbar ist).

### RISK-4: I6 (restart_timer.py) — _send_warning Signatur
MCCountdownTimer überschreibt `_send_warning(message, is_initial, is_final)`. Wenn RestartTimer intern diese Methode anders aufruft (z.B. mit Keyword-Arguments in einer anderen Reihenfolge), bricht die Vererbung.

**Empfehlung:** In I6 die Methode als `async def _send_ingame_warning()` extrahieren (nur den SAT-API-Teil). MCCountdownTimer überschreibt dann nur diese Methode, nicht die gesamte `_send_warning()`.

### RISK-5: HAR-Suppress Timing
HAR wird mit `(countdown_minuten * 60) + 900` Sekunden unterdrückt. Bei 10-Min-Countdown = 1500s = 25 Min. Aber ein Update mit 584 MB Download + NeoForge-Install + 3 Startversuche à 120s kann **45+ Minuten** dauern. Wenn HAR vorher aufwacht, startet er den Server während des Updates.

**Fix:** HAR-Suppress dynamisch verlängern:
```python
# In _set_phase():
if self.har:
    self.har.suppress(900)  # Bei jeder Phase nochmal 15 Min verlängern
```

---

## C. Optimierungen (nice-to-have, nicht blockierend)

### OPT-1: file_manager.py — Download-Resume (HTTP Range)
Aktuell wird bei Fehler die gesamte Datei gelöscht und neu heruntergeladen. Mit `Range: bytes=X-` könnte man fortsetzen. Der Code müsste die Teildownload-Datei behalten und den Hash über die Gesamtdatei berechnen.

### OPT-2: update_manager.py — Progress-Reporting an Discord
Aktuell gibt es keinen Discord-Feedback während des Downloads/Extrahierens. Ein Progress-Embed das sich alle 10% aktualisiert wäre hilfreich:
```python
async def _progress_callback(self, pct, dl_mb, total_mb):
    if self._progress_msg:
        await self._progress_msg.edit(content=f"⬇️ Download: {pct}% ({dl_mb:.0f}/{total_mb:.0f} MB)")
```

### OPT-3: modpack_updater.py — Version-Regex robuster
```python
version_match = re.search(r'v[\d.]+', display_name)
```
Matcht nur `vXX.X`. Was wenn CurseForge den Display-Name ändert? Fallback: `displayName` komplett als Version verwenden.

---

## D. Implementierungs-Reihenfolge (validiert)

Die empfohlene Reihenfolge aus OFFEN.md ist korrekt, mit einer Ergänzung:

1. **BUG-1 bis BUG-7 fixen** (VOR jeder Integration!)
2. **I6** — restart_timer.py (Voraussetzung für MCCountdownTimer)
3. **I4** — server.py (Spielererkennung, braucht nichts anderes)
4. **I5** — discord_notifier.py (DM an Owner, braucht nichts anderes)
5. **I1** — monitor_bot.py (zentrales Wiring, braucht I4, I5, I6)
6. **I3** — chat_bridge.py (In-Game-Befehle, braucht I1 für UpdateManager-Referenz)
7. **I2** — scheduler_cog.py (Zeitplan, braucht I1 für UpdateManager)
8. **I7 + I8** — Discord-Commands (brauchen I1 für UpdateManager)
9. **I9** — ENV-Dokumentation (parallel zu allem)

---

## E. Zusammenfassung

| Kategorie | Anzahl | Kritisch |
|-----------|--------|----------|
| Bugs (muss fixen) | 7 | 3 (BUG-4, BUG-6, BUG-7) |
| Integrations-Risiken | 5 | 2 (RISK-1, RISK-5) |
| Optimierungen | 3 | 0 |

**Kritischste Probleme:**
1. BUG-6: NeoForge wird im falschen Ordner installiert
2. BUG-7: Crash-Recovery macht keinen Rollback
3. BUG-4: Update auf laufendem Server möglich
4. RISK-5: HAR-Suppress zu kurz für lange Updates
