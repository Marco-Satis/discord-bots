# Feature-Plan: Automatisches Update-System (MC + SAT)

> **Version:** 1.6 | **Datum:** 15. März 2026
> **Status:** IMPLEMENTIERUNG — Kern-Module fertig, Integration I1-I9 offen
> **Betroffene Server:** BMC5 (NeoForge), Vanilla (Platzhalter), Satisfactory (SteamCMD)

---

## 1. Übersicht

Vollautomatisches Update-System für Minecraft-Modpacks (CurseForge-API) und Satisfactory (SteamCMD).
Erkennt neue Versionen, lädt Server Packs herunter, führt Updates durch (inkl. NeoForge-Update),
sichert und stellt Custom-Dateien wieder her, und macht automatischen Rollback bei Fehlschlag.

### Kernprinzipien
- **Vollautomatisch:** Kein manueller Eingriff nötig (aber per Discord UND In-Game abbrechbar)
- **Sicher:** 3 Startversuche, automatischer Rollback, DM + E-Mail bei Fehlschlag
- **Generisch:** Gleiche Logik für alle MC-Server (BMC, Vanilla, zukünftige) + SAT
- **Smart:** Erkennt NeoForge-Versionsänderungen und aktualisiert automatisch
- **Cloud-gesichert:** Alle Backups folgen den bestehenden Backup-Regeln und werden in OneDrive hochgeladen
- **Crash-Recovery:** Erkennt abgebrochene Updates beim Neustart und setzt automatisch fort
- **Atomar:** Staging-Verzeichnis → Atomic Swap — kein halbfertiger Zustand möglich
- **Integritätsgeprüft:** SHA1/MD5-Hashes von CurseForge werden nach Download verifiziert

---

## 2. Architektur — UpdateManager Wrapper

### Warum ein Wrapper?
Das Update-System besteht aus vielen Einzelkomponenten (ModpackUpdater, RestartTimer, Backup, Download, etc.).
Der **UpdateManager** ist der zentrale Orchestrator, der alle Teile zusammenführt und den kompletten
Update-Flow steuert — von der Erkennung bis zur Verifikation.

### Klassendiagramm

```
┌──────────────────────────────────────────────────────────────────────┐
│                        UpdateManager                                 │
│  (modules/minecraft/update_manager.py)                              │
│                                                                      │
│  Orchestriert den gesamten Update-Flow für MC + SAT                 │
│                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐       │
│  │ RestartTimer │  │ ModpackUpdater│  │ NeoForgeUpdater      │       │
│  │ (bestehend) │  │ (umgeschrieben│  │ (neu)                │       │
│  │             │  │  → CurseForge)│  │                      │       │
│  │ Countdown   │  │ API-Check     │  │ Installer Download   │       │
│  │ Cancel      │  │ Server Pack   │  │ Version-Erkennung    │       │
│  │ Discord Msg │  │ Download+Hash │  │ Installer Ausführung │       │
│  └─────────────┘  └──────────────┘  └──────────────────────┘       │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐       │
│  │ MCCountdown  │  │ FileManager  │  │ UpdateChecker (SAT)  │       │
│  │ Wrapper (neu)│  │ (neu)        │  │ (bestehend, erweitert│       │
│  │             │  │              │  │  → voller Flow)      │       │
│  │ RCON /title │  │ Streaming DL │  │                      │       │
│  │ Banner      │  │ ZIP-Extrakt. │  │ SteamCMD Check       │       │
│  │ Extends     │  │ Atomic Swap  │  │ SteamCMD Update      │       │
│  │ RestartTimer│  │ Hash-Verify  │  │ Build-ID Vergleich   │       │
│  └──────────────┘  └──────────────┘  └──────────────────────┘       │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐       │
│  │ BackupManager│  │ DiscordNotif.│  │ CrashRecovery        │       │
│  │ (bestehend) │  │ (bestehend + │  │ (neu)                │       │
│  │             │  │  DM-Methode) │  │                      │       │
│  │ World-Backup│  │ Channel-Msgs │  │ Erkennt abgebrochene │       │
│  │ OneDrive    │  │ Owner-DM     │  │ Updates beim Start   │       │
│  │ Rotation    │  │ E-Mail       │  │ Setzt fort mit       │       │
│  └─────────────┘  └──────────────┘  │ 3-Versuche-System   │       │
│                                      └──────────────────────┘       │
│  Querschnitt:                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐       │
│  │ asyncio.Lock │  │ SQLite       │  │ Rate-Limiter         │       │
│  │ pro Server   │  │ Version-Track│  │ CurseForge API       │       │
│  │ (kein konk.) │  │ Update-Log   │  │ Exponential Backoff  │       │
│  └──────────────┘  └──────────────┘  └──────────────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

### Zentrale Klasse: UpdateManager

```python
class UpdateManager:
    """Zentraler Orchestrator für alle Update-Vorgänge."""

    def __init__(self, bot, server_id: str):
        self.bot = bot
        self.server_id = server_id
        self._lock = asyncio.Lock()           # Concurrent-Update-Schutz
        self._active_timer: Optional[MCCountdownTimer] = None
        self._update_state: Optional[str] = None  # Für Crash-Recovery

    async def run_update(self, version_info: dict, trigger: str) -> bool:
        """Kompletter Update-Flow: Countdown → Backup → Update → Verify"""
        async with self._lock:
            # ... orchestriert alle Phasen ...

    async def cancel(self) -> bool:
        """Bricht laufendes Update/Countdown ab"""

    async def check_and_resume(self) -> None:
        """Crash-Recovery: Prüft auf abgebrochene Updates beim Bot-Start"""

    async def get_status(self) -> dict:
        """Aktueller Status für Discord-Commands und Dashboard"""
```

### MCCountdownTimer — Wrapper um RestartTimer

Der bestehende `RestartTimer` (`modules/restart_timer.py`) wird für MC-Server erweitert:
- **RestartTimer** nutzt `api.run_command("say ...")` für SAT In-Game-Nachrichten
- **MCCountdownTimer** nutzt RCON `/title @a` für große MC-Bildschirm-Banner

```python
class MCCountdownTimer(RestartTimer):
    """Erweitert RestartTimer um MC-spezifische RCON /title Banner."""

    def __init__(self, mc_server, channel, update_manager):
        # api=None ist sicher, weil _send_ingame_warning() komplett überschrieben wird
        # und RestartTimer.api nur in _send_ingame_warning() verwendet wird
        super().__init__(api=None, channel=channel)
        self.mc_server = mc_server
        self.update_manager = update_manager

    async def _send_ingame_warning(self, minutes_left: int, action_name: str):
        """Sendet /title Banner statt api.run_command('say ...')"""
        color = "gold" if minutes_left > 2 else "red"
        title_cmd = f'/title @a title {{"text":"SERVER UPDATE","color":"{color}"}}'
        sub_cmd = f'/title @a subtitle {{"text":"in {minutes_left} Minuten","color":"yellow"}}'
        await self.mc_server.rcon_command(title_cmd)
        await self.mc_server.rcon_command(sub_cmd)
```

---

## 3. Update-Zeitplan

| Zeitpunkt | Aktion |
|-----------|--------|
| 12:00 Uhr | Modpack-Check → Bei neuer Version: Sofort Auto-Update mit 10-Min-Countdown |
| 00:00 Uhr | Modpack-Check → Bei neuer Version: Flag in `modpack_updates` setzen (`status='scheduled', update_type='auto_daily'`) |
| 04:00 Uhr | Daily Restart → Falls `modpack_updates` einen scheduled-Eintrag hat: Update ausführen, sonst normaler Restart |
| Manuell | `/mc modpack update [server]` → Sofort mit 10-Min-Countdown |
| In-Game | `!cancel` (nur OPs) → Bricht laufendes Update/Countdown ab |

Updates können jederzeit erfolgen — der 10-Minuten-Countdown gibt Spielern genug Zeit für eine Pause und ihre Arbeit kurz zu beenden.

---

## 4. Update-Ablauf (Detail)

### Phase 0 — Crash-Recovery (beim Bot-Start)
Beim Start prüft der UpdateManager ob ein vorheriges Update abgebrochen wurde:
1. SQLite-Tabelle `modpack_updates` nach Status `"in_progress"` durchsuchen
2. Falls gefunden: Update dort fortsetzen wo es abgebrochen wurde
3. **3-Versuche-System:** Jeder Fortsetzungsversuch zählt als neuer Versuch
4. Nach 3 gescheiterten Versuchen (inkl. vorherige Session): Rollback + Benachrichtigung
5. `update_phase` in SQLite speichert die aktuelle Phase:
   - `"preparing"` — Disk-Check, Server-Stop
   - `"backing_up"` — World-Backup + Cloud-Upload
   - `"downloading"` — Server Pack Download
   - `"extracting"` — ZIP entpacken + Custom-Dateien sichern
   - `"swapping"` — Atomic Swap (alter→Rollback, neu→Production)
   - `"starting"` — Server starten + RCON-Check
   - `"verifying"` — Verifizierung läuft

```python
async def check_and_resume(self):
    """Wird beim Bot-Start aufgerufen"""
    pending = await db.fetch_one(
        "SELECT * FROM modpack_updates WHERE status='in_progress' AND server_id=?",
        (self.server_id,)
    )
    if pending and pending["attempts"] < 3:
        logger.warning(f"Abgebrochenes Update gefunden (Phase: {pending['update_phase']})")
        await self.resume_update(pending)
    elif pending and pending["attempts"] >= 3:
        logger.error(f"Update nach 3 Versuchen fehlgeschlagen — Rollback")
        await self.rollback(pending)
```

### Phase 1 — Erkennung (automatisch, via CurseForge API)
1. **Rate-Limiter:** CurseForge API-Aufrufe mit Exponential Backoff (max 10 Requests/Min)
2. ModpackUpdater prüft **CurseForge API** auf neue Versionen:
   - `GET /v1/mods/{project_id}/files?pageSize=1` → neueste Client-Version
   - CurseForge Projekt-ID: `462042` (BMC5)
   - API-Key: `CURSEFORGE_API_KEY` aus .env (kostenlos, bcrypt-Format)
3. **Versionsvergleich via SQLite** (nicht mehr .env!):
   - Aktuelle File-ID wird in SQLite-Tabelle `server_versions` gespeichert
   - Vergleicht gespeicherte `curseforge_file_id` mit API-Ergebnis `data[0].id`
   - Versionsnummer (z.B. "v48.5") wird aus `displayName` geparst für Anzeige
4. **Server Pack automatisch finden:**
   - Feld `serverPackFileId` im Client-Pack verweist direkt auf den Server Pack
   - `GET /v1/mods/{project_id}/files/{serverPackFileId}` → Download-URL + Dateiname + **SHA1/MD5-Hashes**
   - Beispiel: Client v48.5 (ID 7703808) → Server Pack (ID 7703842) → `BMC5_Server_Pack_v48.5.zip`
5. NeoForge-Versionsänderung prüfen (aus Server Pack `variables.txt` oder NeoForge-Installer)
6. **Concurrent-Lock:** `asyncio.Lock()` pro Server verhindert parallele Updates

**Warum CurseForge statt Modrinth?**
- Modrinth hat NUR `.mrpack` Client-Packs (kein Server Pack)
- Modrinth `version_number` ("v31") ≠ Server Pack Version ("v47") — verschiedene Nummerierungssysteme
- CurseForge hat Client UND Server Pack als separate Dateien mit direkter Verlinkung
- Versionsnummern stimmen überein (v47, v48.5 auf CurseForge = v47, v48.5 auf dem Server)

### Phase 2 — Ankündigung (10 Minuten Countdown via MCCountdownTimer)
**Nutzt bestehenden `RestartTimer` mit MC-Wrapper `MCCountdownTimer`:**
1. **Health-Auto-Restart unterdrücken** via `har.suppress(900)` — MUSS VOR dem Countdown passieren!
2. **Discord:** Nachricht in MC-Textkanal mit `@Minecraft`-Rolle
   - Embed mit: Alte Version → Neue Version, Changelog-Link, Countdown
   - Abbruchmöglichkeit erwähnt
3. **In-Game (RCON via MCCountdownTimer) — Großes Banner mittig auf dem Bildschirm:**
   - 10 Min: `/title @a title {"text":"SERVER UPDATE","color":"gold"}`
     + `/title @a subtitle {"text":"in 10 Minuten — Update auf vXX","color":"yellow"}`
   - 5 Min: `/title @a title {"text":"SERVER UPDATE","color":"gold"}`
     + `/title @a subtitle {"text":"in 5 Minuten","color":"yellow"}`
   - 2 Min: `/title @a title {"text":"SERVER NEUSTART","color":"red"}`
     + `/title @a subtitle {"text":"in 2 Minuten!","color":"red"}`
   - 1 Min: `/title @a title {"text":"SERVER NEUSTART","color":"red"}`
     + `/title @a subtitle {"text":"in 60 Sekunden!","color":"red"}`
   - 30 Sek: `/title @a title {"text":"SERVER NEUSTART","color":"dark_red"}`
     + `/title @a subtitle {"text":"in 30 Sekunden!","color":"red"}`
4. **Abbruch möglich:**
   - Discord: `/mc modpack cancel`
   - In-Game: `!cancel` (nur OPs/Admins)
   - Beide Wege rufen `UpdateManager.cancel()` → `MCCountdownTimer.cancel()` auf
   - Entwarnung in Discord + In-Game Banner `{"text":"Update abgebrochen","color":"green"}`

### Phase 3 — Vorbereitung
**Hinweis:** HAR-Suppress passiert bereits in Phase 2 (vor dem Countdown)!
1. **Disk-Space Pre-Flight:** `psutil.disk_usage("/")` — mindestens 1.2 GB frei (584 MB ZIP + Extraktion + Overhead)
   - Bei zu wenig Speicher: Update abbrechen, Warnung an Owner-DM
2. **Update-State in SQLite setzen:** `status='in_progress', update_phase='preparing'`
3. `save-all` via RCON
4. 5 Sekunden warten (Chunks fertig schreiben lassen)
5. Server stoppen via RCON `stop` (graceful shutdown des Java-Prozesses)
6. Warten bis `systemctl is-active` → `inactive` meldet (Polling alle 2s, max 30s)
7. **Fallback:** Falls nach 30s noch aktiv → `systemctl stop {service}` als Force-Stop

### Phase 4 — Backup (bestehende Systeme nutzen!)
1. **World-Backup erstellen** via bestehenden `MinecraftBackupManager` (`modules/minecraft/backup.py`)
   - Nutzt `shutil.copytree` mit Metadaten-JSON
   - Rotation nach bestehender Regel (max 20 Backups)
2. **World-Backup in OneDrive hochladen** via bestehenden `OneDriveBackup` (`modules/backup/onedrive_backup.py`)
   - Nutzt `rclone copyto` für Cloud-Upload
   - Cloud-Rotation nach bestehender Regel
3. Kompletten `mods/` Ordner sichern → `mods_rollback_{version_id}/`
4. Kompletten `config/` Ordner sichern → `config_rollback_{version_id}/`

### Phase 5 — Custom-Dateien sichern
Folgende Dateien werden VOR dem Entpacken gesichert:
- `server.properties`
- `user_jvm_args.txt`
- `ops.json`
- `whitelist.json`
- `banned-players.json`
- `banned-ips.json`

(Konfigurierbar per ENV: `MC_{ID}_PRESERVE_FILES`)

### Phase 6 — Update durchführen (Atomic Swap)
1. **Update-State:** `update_phase='downloading'` in SQLite
2. **Streaming Download** in Staging-Verzeichnis (`/home/minecraft/.update_staging/`):
   - `aiohttp` mit 8 KB Chunks und Progress-Callback
   - Download von `edge.forgecdn.net` (CurseForge CDN)
   - Timeout: 60 Minuten (für 584 MB bei langsamer Verbindung)
   - **Streaming-Hash:** SHA1-Hashwert wird *während* des Downloads berechnet (`hashlib.sha1.update(chunk)` pro Chunk) — spart eine komplette 584 MB Datei-Lesung
   - **Download-Resume:** Bei Abbruch/Timeout wird die Teildownload-Datei behalten. Beim Retry: `Range: bytes={offset}-` Header → nur den Rest herunterladen (CurseForge CDN unterstützt Range Requests). Hash wird in dem Fall nach komplettem Download über die Gesamtdatei verifiziert.
3. **Hash-Verifikation** (inline während Download):
   - CurseForge liefert `hashes[]` mit SHA1 und MD5 pro Datei
   - Vergleich: Streaming-SHA1 vs. API-Hash (nach letztem Chunk)
   - Bei Mismatch: Datei löschen + Download komplett wiederholen (max 3x), dann abbrechen
4. **Update-State:** `update_phase='extracting'` in SQLite
5. **ZIP entpacken** in Staging-Unterordner (nicht direkt in Server-Ordner!):
   - `zipfile.ZipFile` via `asyncio.to_thread()` für non-blocking
   - Integritätsprüfung via `zf.testzip()` vor Extraktion
6. **Atomic Swap:**
   a. Gesicherte Custom-Dateien (Phase 5) in extrahierten Ordner zurückschreiben
   b. Falls NeoForge-Version geändert (aus Server Pack `variables.txt` prüfen):
      - NeoForge-Installer von offizieller URL herunterladen
      - Installer ausführen: `java -jar neoforge-installer.jar --installServer`
      - Verifizieren dass `run.sh` aktualisiert wurde
   c. `sudo mv` alter Server-Ordner → Rollback-Ordner
   d. `sudo mv` Staging-Ordner → Server-Ordner
   e. `sudo chown -R minecraft:minecraft` für korrekte Berechtigungen
7. **Version in SQLite aktualisieren** (NICHT in .env!):
   - `server_versions` Tabelle: `curseforge_file_id`, `display_version`, `updated_at`
   - Vorteil: Kein .env-Rewrite nötig, ConfigReloader wird nicht gestört
   - Bei Bot-Start wird Version aus SQLite geladen

### Phase 7 — Verifizierung (3 Versuche)

**Versuch 1-3:**
1. Server starten via `systemctl start {service}`
2. Warte max **120 Sekunden** auf RCON-Erreichbarkeit (RCON-Polling alle 4s, 30 Versuche)
3. Bei Erfolg → Fertig, weiter zu Phase 8
4. Bei Fehlschlag → Server stoppen, 10s warten, nächster Versuch

> **Hinweis:** Die bestehende `_safe_start()` in update_checker.py nutzt 90s Timeout.
> Für MC-Server (NeoForge) brauchen wir 120s, da diese langsamer starten als SAT.

**Nach 3 Fehlschlaegen → Rollback:**
1. `mods/` und `config/` aus Rollback-Ordner wiederherstellen
2. Falls NeoForge aktualisiert wurde: alten NeoForge-Installer ausfuehren
3. Server mit alter Version starten
4. Benachrichtigungen senden (siehe Phase 8)

### Phase 8 — Benachrichtigungen

**Bei Erfolg:**
- Discord: Embed im MC-Textkanal "Update auf vXX erfolgreich" mit @Minecraft-Ping
- In-Game: `/title @a title {"text":"Server aktualisiert!","color":"green"}` + Subtitle mit neuer Version
- SQLite: Update-Log-Eintrag

**Bei Fehlschlag (nach Rollback):**
- Discord DM an Owner mit detailliertem Fehlerbericht (**NEU — muss implementiert werden**, siehe §15)
- E-Mail an Admin via bestehenden `EmailNotifier` (`modules/notifications/email_notifier.py`)
- Discord: Admin-Log-Kanal Warnung via bestehenden `DiscordNotifier`
- SQLite: Update-Log-Eintrag mit Fehlerstatus

---

## 5. CurseForge API — Versionserkennung und Server Pack Download

### Warum CurseForge (nicht Modrinth)?
| Aspekt | Modrinth | CurseForge |
|--------|----------|------------|
| Server Pack vorhanden? | **NEIN** — nur `.mrpack` Client-Pack | **JA** — separater Download |
| Version-Matching | "v31" ≠ "v47" (verschiedene Systeme) | "v48.5" = "v48.5" (stimmt überein) |
| Server Pack Verlinkung | N/A | `serverPackFileId` im Client-Pack |
| API-Key nötig? | Nein | Ja (kostenlos) |

### API-Flow (verifiziert am 13.03.2026)

**Schritt 1:** Neueste Version abfragen
```
GET https://api.curseforge.com/v1/mods/462042/files?pageSize=1
Header: x-api-key: {CURSEFORGE_API_KEY}
```
Antwort liefert:
- `data[0].id` = 7703808 (Client-Pack File-ID)
- `data[0].displayName` = "BMC5 [NEOFORGE] 1.21.1 v48.5"
- `data[0].serverPackFileId` = 7703842 (Server Pack File-ID!)
- `data[0].gameVersions` = ["1.21", "1.21.1", "NeoForge"]

**Schritt 2:** Server Pack Details abrufen
```
GET https://api.curseforge.com/v1/mods/462042/files/7703842
Header: x-api-key: {CURSEFORGE_API_KEY}
```
Antwort liefert:
- `data.fileName` = "BMC5_Server_Pack_v48.5.zip"
- `data.downloadUrl` = "https://edge.forgecdn.net/files/7703/842/BMC5_Server_Pack_v48.5.zip"
- `data.fileLength` = 612,730,880 (584.3 MB)
- `data.isServerPack` = true

**Schritt 3:** Versionsvergleich
- Gespeichert: `MC_BMC_CURSEFORGE_FILE_ID=7449464` (v47, aktuell installiert)
- API liefert: `id=7703808` (v48.5, neueste)
- 7703808 ≠ 7449464 → **Update verfuegbar!**
- Display-Version "v48.5" aus `displayName` parsen (Regex: `v[\d.]+`)

### ENV-Variablen (CurseForge)
```
CURSEFORGE_API_KEY=$2a$10$...       # CurseForge API-Key (bcrypt-Format)
MC_BMC_MODPACK_SOURCE=curseforge    # Quelle: curseforge (statt modrinth)
MC_BMC_CURSEFORGE_PROJECT_ID=462042 # CurseForge Projekt-ID
MC_BMC_CURSEFORGE_FILE_ID=7449464   # Aktuell installierte Client-Pack File-ID (v47)
MC_BMC_MODPACK_VERSION=v47          # Display-Name für Discord/In-Game
```

### Verifizierte Daten (Stand 13.03.2026)
| Version | Client-Pack ID | Server Pack ID | Server Pack Datei | Größe |
|---------|---------------|----------------|-------------------|---------|
| v48.5 | 7703808 | 7703842 | BMC5_Server_Pack_v48.5.zip | 584 MB |
| v48 | 7689233 | 7693801 | BMC5_Server_Pack_v48.zip | ~584 MB |
| v47 (installiert) | 7449464 | 7449688 | BMC5_Server_Pack_v47.zip | ~584 MB |

---

## 6. In-Game Befehle (MC → Bot)

### Architektur
Die bestehende Chat-Bridge (`modules/minecraft/chat_bridge.py`) pollt bereits das MC-Log und erkennt Chat-Nachrichten via `CHAT_RE`. Die In-Game-Befehle werden dort integriert:

1. In `_process_log_content()` werden Chat-Nachrichten mit `!`-Prefix abgefangen
2. Berechtigung wird über die OP-Liste geprüft (`ops.json` im Server-Ordner)
3. Antworten gehen via RCON zurück: `/tellraw @a` (formatiert) oder `/say` (einfach)
4. Die Chat-Bridge bekommt eine Referenz auf `MinecraftServer` für RCON-Zugriff
5. **SpielerName420 ist als Default-OP gesetzt** (bereits in `ops.json` auf dem Server)

### Implementierung in chat_bridge.py

```python
# Neues Regex für Befehle (nutzt bestehendes _TS + _TH)
COMMAND_RE = re.compile(
    rf'{_TS}\s+{_TH}<(\w+)>\s+!(\w+)(?:\s+(.*))?$',
    re.MULTILINE
)
```

In `_process_log_content()` nach Chat-Check:
```python
# In-Game Befehle
for match in COMMAND_RE.finditer(content):
    player = match.group(1)
    command = match.group(2).lower()
    args = match.group(3) or ""
    await self._handle_ingame_command(player, command, args)
```

### Verfuegbare Befehle

| Befehl | Berechtigung | Beschreibung | Antwort-Format |
|--------|-------------|--------------|----------------|
| `!status` | Alle | Server-Status (Spieler, Uptime, TPS/Tick-Rate) | `/tellraw @a` mit farbigem JSON |
| `!version` | Alle | Aktuelle Modpack-Version + ob Update verfuegbar | `/tellraw @a` |
| `!players` | Alle | Spielerliste ALLER MC-Server (BMC + Vanilla) | `/tellraw @a` |
| `!tps` | Alle | Aktuelle Tick-Rate (Performance-Check) | `/tellraw @a` |
| `!cancel` | OP | Bricht laufendes Update/Countdown ab | `/say` Bestaetigung |
| `!restart` | OP | Server-Neustart mit 5min Countdown | `/title @a` Banner + `/say` |
| `!backup` | OP | Sofortiges World-Backup ausloesen | `/say` Bestaetigung + Ergebnis |
| `!help` | Alle | Zeigt alle verfuegbaren In-Game-Befehle | `/tellraw @a` |

### OP-Prüfung
```python
async def _is_op(self, player: str) -> bool:
    """Prueft ob Spieler OP-Rechte hat via ops.json (MC-Server-Datei)"""
    ops_file = self.server_path / "ops.json"
    if ops_file.exists():
        ops = json.loads(ops_file.read_text())
        return any(op["name"].lower() == player.lower() for op in ops)
    return False
```

**Hinweis:** Die OP-Prüfung liest direkt die `ops.json` des jeweiligen Servers.
Wer in MC OP ist, hat automatisch auch OP-Berechtigung für die Bot-Befehle.
SpielerName420 muss auf jedem Server als OP eingetragen sein.

### Beispiel-Antwort (tellraw)
```json
/tellraw @a [
  {"text":"[BOT] ","color":"gold","bold":true},
  {"text":"Server Status: ","color":"white"},
  {"text":"Online","color":"green"},
  {"text":" | Spieler: ","color":"white"},
  {"text":"3/20","color":"aqua"},
  {"text":" | Version: ","color":"white"},
  {"text":"v47","color":"yellow"}
]
```

### Wichtig: Chat-Bridge braucht neue Abhängigkeiten
- Referenz auf `MinecraftServer`-Instanz (für RCON-Zugriff)
- Referenz auf `UpdateManager` (für `!cancel` und `!version`)
- Server-Pfad für `ops.json` Zugriff
- Diese werden beim Bridge-Setup in `bots/monitor_bot.py` übergeben

---

## 7. Spielererkennung-Verbesserungen

### Problem 1: RCON `list` Parsing
NeoForge-Server geben möglicherweise ein anderes Format zurück als Vanilla.
Aktueller Code in `modules/minecraft/server.py` (Zeile 316-334) nutzt einfaches String-Split:
```python
parts = response.lower().split(" of max ")
```
Das matcht nur "X of max Y" aber nicht "X of a max of Y" oder "X/Y".

### Lösung
Regex-basiertes Parsing in `get_player_count()`:
```python
import re

async def get_player_count(self) -> Tuple[int, int]:
    try:
        response = await self.rcon_command("list")
        # Matcht alle bekannten Formate:
        # "There are 2 of a max of 20 players online: ..."
        # "There are 2 of max 20 players online: ..."
        # "There are 2/20 players online: ..."
        match = re.search(r'There are (\d+)(?:\s+of\s+(?:a\s+)?max\s+(?:of\s+)?|/)(\d+)', response)
        if match:
            return int(match.group(1)), int(match.group(2))
        # Fallback: Nur Online-Zahl
        match = re.search(r'There are (\d+)', response)
        if match:
            return int(match.group(1)), await self._get_max_players_fallback()
        return 0, await self._get_max_players_fallback()
    except Exception as e:
        logger.debug(f"[{self.server_id}] Spielerzahl nicht parsbar: {e}")
        return 0, 20
```

### Problem 2: Max-Player nicht in Discord angezeigt
Voice-Channel und Status-Embed zeigen nicht immer die korrekte Spielerzahl.

### Lösung
1. `max_players` Fallback aus `server.properties`:
```python
async def _get_max_players_fallback(self) -> int:
    """Liest max-players aus server.properties als Fallback"""
    props = await self.get_properties()  # Existiert bereits!
    return int(props.get("max-players", "20"))
```

2. Voice-Channel-Name aktualisieren (in `bots/monitor_bot.py`):
   - Aktuelles Format: `"MC BMC: 2 Online"`
   - Neues Format: `"MC BMC: 2/20 Online"`
   - Änderung in der Voice-Channel-Update-Logik: `get_player_count()` gibt jetzt `(online, max)` zurück

3. Status-Embed aktualisieren:
   - Spielerzahl-Feld: `"2/20 Spieler"` statt `"2 Spieler"`

### Problem 3: NeoForge Spielererkennung — Funktionsweise und Robustheit

NeoForge (1.21.1) schreibt Join/Leave-Events ins Server-Log, allerdings in einem anderen Format als Vanilla:

**Vanilla:**
```
[21:00:50] [Server thread/INFO]: SpielerName420 joined the game
```

**NeoForge:**
```
[12Mar2026 21:00:50.857] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer/]: SpielerName420 joined the game
```

Unser Regex (`_TS + _TH`) matcht bereits BEIDE Formate — das wurde in der BMC5-Migration verifiziert und funktioniert. Das frühere Problem war kein Regex-Problem sondern `systemd ReadWritePaths` auf den falschen Pfad.

**Trotzdem:** RCON-Polling als robuster Fallback, weil:
- Manche NeoForge-Mods können die Log-Ausgabe verändern
- RCON `list` funktioniert immer, unabhängig vom Log-Format
- Doppelte Erkennung schadet nicht (Deduplizierung im Player-Tracker)

### Lösung: Dual-Erkennung
- **Primär:** Log-basierte Erkennung via Chat-Bridge (sofort, <5 Sekunden Latenz)
- **Sekundär:** RCON `list`-Polling alle 30 Sekunden als Fallback
- Player-Tracker vergleicht aktuelle Liste mit vorheriger und erkennt Joins/Leaves
- Deduplizierung: Wenn Log-Erkennung und RCON-Polling den gleichen Event melden → nur einmal an Discord senden

---

## 8. Discord-Befehle

### Neue/Erweiterte Commands

| Befehl | Bot | Berechtigung | Beschreibung |
|--------|-----|-------------|--------------|
| `/mc modpack status [server]` | Monitor | Spieler | Zeigt Version, Update-Status, letzter Check |
| `/mc modpack update [server]` | Monitor | Admin | Startet manuelles Update mit 10min Countdown |
| `/mc modpack cancel` | Monitor | Admin | Bricht laufendes Update/Countdown ab |
| `/mc modpack rollback [server]` | Monitor | Owner | Manueller Rollback auf vorherige Version |
| `/mc modpack history [server]` | Monitor | Spieler | Update-Historie aus SQLite |
| `/mc modpack check [server]` | Monitor | Admin | Sofortiger Check ohne Auto-Update |

### Beispiel: `/mc modpack status`
```
┌──────────────────────────────────────┐
│ 📦 Modpack-Status: BMC5             │
├──────────────────────────────────────┤
│ Version:    v47                      │
│ Quelle:     CurseForge (NeoForge)   │
│ Game:       1.21.1                   │
│ Update:     ✅ Keine neue Version    │
│ Letzter Check: Vor 2 Stunden        │
│ NeoForge:   21.1.217                │
└──────────────────────────────────────┘
```

---

## 9. Datenbank

### Neue SQLite-Tabelle: `modpack_updates` (Update-Log)

```sql
CREATE TABLE IF NOT EXISTS modpack_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id TEXT NOT NULL,              -- "BMC", "VANILLA", "SAT"
    old_version TEXT NOT NULL,            -- "v47" (Display-Name)
    new_version TEXT NOT NULL,            -- "v48.5" (Display-Name)
    old_curseforge_id INTEGER,            -- CurseForge File-ID (alt)
    new_curseforge_id INTEGER,            -- CurseForge File-ID (neu)
    update_type TEXT NOT NULL,            -- "auto_12h", "auto_daily", "manual"
    status TEXT NOT NULL,                 -- "scheduled", "in_progress", "success", "failed", "rolled_back"
    update_phase TEXT,                    -- Aktuelle Phase für Crash-Recovery
    neoforge_updated BOOLEAN DEFAULT 0,
    old_neoforge TEXT,                    -- "21.1.217"
    new_neoforge TEXT,                    -- "21.1.220"
    attempts INTEGER DEFAULT 0,           -- Anzahl Startversuche (inkl. Crash-Recovery)
    error_message TEXT,                   -- Fehlerbeschreibung bei Fehlschlag
    backup_path TEXT,                     -- Pfad zum World-Backup
    rollback_path TEXT,                   -- Pfad zu mods/config Rollback
    download_url TEXT,                    -- CurseForge Download-URL
    download_hash_sha1 TEXT,              -- SHA1-Hash für Integritätsprüfung
    file_size_bytes INTEGER,              -- Größe des Server Pack ZIP
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    duration_seconds INTEGER
);
```

### Neue SQLite-Tabelle: `server_versions` (Version-Tracking)

```sql
CREATE TABLE IF NOT EXISTS server_versions (
    server_id TEXT PRIMARY KEY,           -- "BMC", "VANILLA", "SAT"
    display_version TEXT NOT NULL,        -- "v48.5" (für Discord/In-Game Anzeige)
    curseforge_file_id INTEGER,           -- CurseForge Client-Pack File-ID (MC)
    curseforge_server_file_id INTEGER,    -- CurseForge Server-Pack File-ID (MC)
    steam_buildid TEXT,                   -- SteamCMD Build-ID (SAT)
    neoforge_version TEXT,                -- "21.1.220" (MC NeoForge)
    updated_at TIMESTAMP NOT NULL,
    updated_by TEXT DEFAULT 'system'      -- "auto", "manual", "import"
);
```

**Warum SQLite statt .env für Version-Tracking?**
- `.env` programmatisch zu ändern ist fragil (Escaping, Format, Race Conditions)
- `ConfigReloader` überwacht nur `config.json`, nicht `.env`
- SQLite ist atomar, Thread-safe und bereits die primäre Datenquelle
- Bei Bot-Start: Version aus `server_versions` laden statt aus ENV
- `.env` behält die initialen Werte als Fallback für Fresh-Install

### Retention
- Erfolgreiche Updates: Unbegrenzt (SQLite)
- Fehlgeschlagene Updates: 90 Tage (SQLite) — **Cleanup via `scheduler_cog.py` Daily-Task** (bestehender Feature-F63-Cleanup erweitern)
- Scheduled-Einträge die nie ausgeführt wurden: 7 Tage (SQLite) — gleicher Cleanup
- Rollback-Dateien auf Disk: 2 Versionen (ältere werden bei neuem Update gelöscht — via UpdateManager nach erfolgreichem Update)
- Server Pack ZIPs: 2 Versionen (ältere werden bei neuem Download gelöscht — via FileManager nach erfolgreichem Download)

---

## 10. Neue/Geänderte Dateien

### Neue Dateien
| Datei | Beschreibung | Status |
|-------|-------------|--------|
| `modules/minecraft/update_manager.py` (32 KB) | **Zentraler Orchestrator** — steuert kompletten Update-Flow (Phase 0-8), Crash-Recovery, Concurrent-Lock | Fertig (lokal) |
| `modules/minecraft/mc_countdown.py` (11 KB) | **MCCountdownTimer** — erweitert RestartTimer um RCON `/title` Banner für MC | Fertig (lokal) |
| `modules/minecraft/neoforge_updater.py` (10 KB) | NeoForge-Installer Download + Ausführung + Versions-Erkennung | Fertig (lokal) |
| `modules/minecraft/file_manager.py` (24 KB) | **FileManager** — Streaming Download, ZIP-Extraktion, Hash-Verifikation, Atomic Swap | Fertig (lokal) |

### Geänderte Dateien
| Datei | Änderung | Status |
|-------|-----------|--------|
| `modules/minecraft/modpack_updater.py` (15 KB) | **Komplett umgeschrieben** → CurseForge API, Server Pack via `serverPackFileId`, Rate-Limiter, Hash-Abruf | Fertig (lokal) |
| `modules/database/migrations.py` (19 KB) | `modpack_updates` + `server_versions` Tabellen (Migration v4) | Fertig (lokal) |
| `modules/monitoring/update_checker.py` (15 KB) | **SAT perform_update() vervollständigt** — HAR-Suppress, Backup-Hook, Server-Stop/Start, Health-Check | Fertig (lokal) |
| `modules/restart_timer.py` | `_send_ingame_warning()` als überschreibbare Methode extrahieren | **Offen (I6)** |
| `modules/minecraft/server.py` | `get_player_count()` Regex-Fix, `_get_max_players_fallback()`, Voice-Channel-Format | **Offen (I4)** |
| `modules/notifications/discord_notifier.py` | Neue Methode `send_dm_to_owner()` für Fehlschlag-DM | **Offen (I5)** |
| `bots/monitor_bot.py` | UpdateManager-Integration, Chat-Bridge RCON-Referenz, Voice-Channel "X/Y" Format, Crash-Recovery beim Start | **Offen (I1)** |
| `modules/minecraft/chat_bridge.py` | In-Game-Befehle (`!status`, `!cancel` etc.), RCON-Referenz, Command-Regex | **Offen (I3)** |
| `cogs/scheduler_cog.py` | Update-Zeitplan (12:00/00:00), Daily-Restart-Integration, SAT-Zeitplan | **Offen (I2)** |
| `cogs/monitor_cog.py` oder neues Cog | `/mc modpack` + `/sat update` Slash-Commands | **Offen (I7+I8)** |
| `config/.env.example` | Neue ENV-Variablen dokumentieren | **Offen (I9)** |

### Neue ENV-Variablen
```env
# CurseForge API (kostenlos, bcrypt-Format)
CURSEFORGE_API_KEY=$2a$10$...       # API-Key von https://console.curseforge.com/

# Modpack-Update-System — BMC5 (NeoForge)
MC_BMC_MODPACK_SOURCE=curseforge
MC_BMC_CURSEFORGE_PROJECT_ID=462042
MC_BMC_CURSEFORGE_FILE_ID=7449464   # Aktuell installiert (v47) — wird bei Update automatisch aktualisiert
MC_BMC_MODPACK_VERSION=v47          # Display-Name für Discord/In-Game
MC_BMC_PRESERVE_FILES=server.properties,user_jvm_args.txt,ops.json,whitelist.json,banned-players.json,banned-ips.json

# Modpack-Update-System — Vanilla (Platzhalter, noch nicht konfiguriert)
MC_VANILLA_MODPACK_SOURCE=curseforge
MC_VANILLA_CURSEFORGE_PROJECT_ID=   # Wird gesetzt wenn Modpack feststeht
MC_VANILLA_CURSEFORGE_FILE_ID=
MC_VANILLA_MODPACK_VERSION=
MC_VANILLA_PRESERVE_FILES=server.properties,user_jvm_args.txt,ops.json,whitelist.json,banned-players.json,banned-ips.json

# Discord — Update-Pings nutzen vorerst die Satisfactory-Rolle
# SATISFACTORY_ROLE_ID=1000000000000000004  (bereits in .env, wird für MC-Pings mitgenutzt)
# Update-Nachrichten gehen in die bestehenden Game-Chat-Kanäle:
# MC_BMC_GAME_CHAT_CHANNEL_ID=1000000000000000006
# MC_VANILLA_GAME_CHAT_CHANNEL_ID=1000000000000000007

# Bot Owner (für DM bei kritischen Fehlern)
BOT_OWNER_ID=1000000000000000001   # Marco's Discord User-ID
```

---

## 11. Backup-Regeln (angepasst an bestehende Systeme)

### Bestehende Module die genutzt werden
- `MinecraftBackupManager` (`modules/minecraft/backup.py`) — World-Backup mit Metadaten + Rotation
- `OneDriveBackup` (`modules/backup/onedrive_backup.py`) — Cloud-Upload via rclone + Cloud-Rotation

### Backup-Matrix

| Backup-Typ | Speicherort | Retention | Cloud (OneDrive) | Modul |
|------------|------------|-----------|-------------------|-------|
| World-Backup (vor Update) | `{server}_backups/` | 20 Backups (bestehende Regel) | Ja — automatischer Upload | MinecraftBackupManager + OneDriveBackup |
| mods/ Rollback | `{server_path}/mods_rollback_{id}/` | **2 Versionen** | Nein | update_manager.py (shutil.copytree) |
| config/ Rollback | `{server_path}/config_rollback_{id}/` | **2 Versionen** | Nein | update_manager.py (shutil.copytree) |
| Server Pack ZIP | `{server_path}/server_packs/` | **2 Versionen** | Nein | modpack_updater.py |
| Custom-Dateien | Temporär (im RAM/tmp) | Nur während Update | Nein | update_manager.py |

### Retention-Cleanup (versionsbasiert, nicht zeitbasiert)
Rollback-Ordner und Server Pack ZIPs werden nach **Anzahl Versionen** bereinigt, nicht nach Zeit:
- **Immer die letzten 2 Versionen behalten** (aktuelle + eine vorherige)
- Wenn Version 3 installiert wird → Rollback von Version 1 löschen
- Vorteil: Bei mehreren schnellen Hotfix-Updates sammeln sich keine unnötigen Ordner an
- Vorteil: Es gibt IMMER eine Rollback-Option, egal wie alt die letzte Version ist

Cleanup-Integration in bestehenden Retention/Cleanup (Feature F63) in `cogs/scheduler_cog.py`.

---

## 12. Fehlerbehandlung

| Fehler | Reaktion |
|--------|----------|
| CurseForge API nicht erreichbar | Retry in 30 Min, kein Update, Warning im Log |
| Download fehlgeschlagen | Retry 3x mit 30s Pause, dann abbrechen + Benachrichtigung |
| ZIP korrupt/entpacken fehlgeschlagen | Abbrechen, Rollback (mods + config), DM + E-Mail |
| NeoForge-Installer fehlgeschlagen | Abbrechen, Rollback (mods + config + NeoForge), DM + E-Mail |
| Server startet nicht (3 Versuche) | Rollback auf alte mods/config, DM + E-Mail |
| RCON nicht erreichbar nach Start | Zaehlt als Fehlstart |
| Kein Server Pack (serverPackFileId=null) | Warnung loggen, kein Update |
| Spieler online bei Update-Start | Countdown laeuft trotzdem (10 Min reichen) |
| Cloud-Upload fehlgeschlagen | Update trotzdem fortsetzen, Warning loggen (lokales Backup existiert) |
| Abbruch via Discord/In-Game | Countdown sofort abbrechen, Entwarnung in Discord + In-Game |

---

## 13. Sicherheitsmaßnahmen

- **HAR-Suppress VOR Countdown** — wird 15 Min vor dem Update unterdrückt (nicht erst beim Server-Stop)
- **Disk-Space Pre-Flight** — mindestens 1.2 GB frei vor Update-Start
- **Concurrent-Lock** — `asyncio.Lock()` pro Server verhindert parallele Updates
- **Hash-Verifikation** — SHA1/MD5 von CurseForge werden nach Download geprüft (3 Versuche bei Mismatch)
- **Atomic Swap** — Dateien werden in Staging entpackt und dann atomar verschoben (kein halbfertiger Zustand)
- **Crash-Recovery** — Abgebrochene Updates werden beim Bot-Neustart erkannt und fortgesetzt (3-Versuche-System)
- World-Backup + Cloud-Upload VOR jeder Änderung
- Custom-Dateien werden IMMER gesichert und wiederhergestellt
- Rollback-Ordner: 2 Versionen behalten (versionsbasiert)
- NeoForge-Installer wird nur ausgeführt wenn sich die Version tatsächlich geändert hat
- **SQLite Version-Tracking** — Versionsdaten in DB statt .env (atomar, Thread-safe)
- **CurseForge Rate-Limiter** — Exponential Backoff für API-Aufrufe
- Update-Log in SQLite für Audit-Trail
- Abbruch per Discord-Command ODER In-Game `!cancel` jederzeit während Countdown möglich
- Bei Abbruch: Alle Spieler werden informiert (Discord + In-Game Banner)

---

## 14. Entscheidungen

### Geklärt
- [x] **Update-Channel:** Bestehende Chat-Bridge-Kanäle nutzen (BMC: `1000000000000000006`, Vanilla: `1000000000000000007`)
- [x] **Ping-Rolle:** Bestehende Satisfactory-Rolle mitnutzen (`SATISFACTORY_ROLE_ID=1000000000000000004`) — später eigene MC-Rolle möglich
- [x] **Owner User-ID:** `BOT_OWNER_ID=1000000000000000001` (Marco)
- [x] **Backup-Retention:** 2 Versionen behalten (versionsbasiert, nicht zeitbasiert)
- [x] **Default-OP:** SpielerName420 auf allen MC-Servern als OP (via ops.json)
- [x] **SAT-Updater:** Vollständiges Auto-Update via SteamCMD (nicht nur Check)

### Noch offen
- [ ] Vanilla-Server Loader und CurseForge Projekt-ID: Wird erst entschieden wenn Modpack feststeht. Nur Platzhalter im Code.
- [ ] `MC_BMC_CURSEFORGE_FILE_ID=7449464` initial in .env setzen — Wert bekannt, Deployment nötig (siehe §17)
- [ ] `CURSEFORGE_API_KEY` in .env setzen — Key ist vorhanden, Deployment nötig (siehe §17)
- [ ] `BOT_OWNER_ID=1000000000000000001` in .env setzen — Wert bekannt, Deployment nötig (siehe §17)

---

## 15. Neue Implementierung: DM an Owner

### Problem
Der bestehende `DiscordNotifier` kann nur in Channels posten — es gibt keine DM-Funktionalität.
Bei kritischen Fehlschlaegen (Update fehlgeschlagen + Rollback) soll der Owner eine persoenliche DM bekommen.

### Lösung
Neue Methode in `modules/notifications/discord_notifier.py`:

```python
async def send_dm_to_owner(self, title: str, description: str,
                           level: NotifyLevel = NotifyLevel.CRITICAL,
                           fields: Optional[List[Tuple[str, str, bool]]] = None) -> None:
    """Sendet eine DM an den Bot-Owner (für kritische Fehler)"""
    owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
    if not owner_id:
        logger.warning("BOT_OWNER_ID nicht konfiguriert — DM nicht gesendet")
        return

    try:
        user = await self.bot.fetch_user(owner_id)
        _, color, emoji = level.value
        embed = discord.Embed(
            title=f"{emoji} {title}",
            description=description,
            color=color,
            timestamp=datetime.now(),
        )
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
        await user.send(embed=embed)
    except discord.Forbidden:
        logger.warning("Owner hat DMs deaktiviert — Benachrichtigung nur via Channel")
    except Exception as e:
        logger.error(f"DM an Owner fehlgeschlagen: {e}")
```

Neue ENV-Variable: `BOT_OWNER_ID` (Discord User-ID)

---

## 16. Satisfactory Auto-Update (SteamCMD)

### Bestehende Infrastruktur
Der SAT-Updater existiert bereits vollständig in `modules/monitoring/update_checker.py`:
- `UpdateChecker.check()` — Vergleicht installierte vs. verfuegbare SteamCMD Build-ID
- `UpdateChecker.perform_update()` — Fuehrt `steamcmd +app_update 1690800 validate` aus
- Callback `on_update_available` ist bereits implementiert
- `/sat update` Discord-Befehl existiert bereits

### Bereits implementiert: perform_update() (v4.0.1)
Die `perform_update()` in `modules/monitoring/update_checker.py` wurde bereits vervollständigt:
- HAR-Suppress vor Server-Stop (verhindert Auto-Restart während Update)
- Server-Start nach Update mit Timeout (90s via `_safe_start()`)
- SteamCMD `+app_update 1690800 validate` Ausführung
- Server-Neustart nach Update + Health-Check
- Robustere Output-Prüfung (nicht nur "success" String)
- Backup-Hook vor Update

### Noch offen: Integration in Update-Manager-Flow

1. **Gleicher Zeitplan:** 12:00/00:00 Check → Bei Update: 10-Min-Countdown
2. **HAR-Suppress VOR Countdown** (nicht erst beim Server-Stop!)
3. **Countdown:** In-Game-Warnung via bestehenden `RestartTimer` mit SAT API `RunCommand("say ...")` + Discord
4. **Backup VOR Update:**
   - World-Save via SAT HTTPS API (`/api/v1/SaveGame`)
   - World-Backup via bestehenden Backup-Manager
   - Cloud-Upload via OneDrive
5. **Update-Ablauf (vervollständigt):**
   a. HAR-Suppress verifizieren (bereits aktiv aus Schritt 2)
   b. Server stoppen (`systemctl stop satisfactory`)
   c. Warten bis Prozess beendet (max 30s)
   d. `steamcmd +app_update 1690800 validate` als `satisfactory`-User
   e. Exit-Code UND Output prüfen (nicht nur String-Match)
   f. Server starten (`systemctl start satisfactory`)
   g. Health-Check: HTTPS API `QueryServerState` erreichbar? (max 120s warten)
   h. Build-ID nach Update aus Manifest lesen und mit vorheriger vergleichen
6. **Rollback:** Bei SteamCMD nicht direkt möglich — stattdessen Benachrichtigung + manuelle Intervention
7. **Benachrichtigungen:** Discord (Satisfactory-Rolle ping) + DM bei Fehlschlag
8. **Abbruch:** `/sat update cancel` Discord-Befehl
9. **Version in SQLite aktualisieren:** `server_versions` Tabelle mit neuer `steam_buildid`

### Unterschiede zu MC-Update
| Aspekt | Minecraft | Satisfactory |
|--------|-----------|-------------|
| Update-Quelle | CurseForge API (Server Pack ZIP) | SteamCMD (automatisch) |
| Rollback | mods/config wiederherstellen | Nicht automatisch (SteamCMD kennt kein Downgrade) |
| NeoForge-Update | Ja | N/A |
| In-Game-Warnung | RCON `/title` (Banner) | HTTPS API `RunCommand("say ...")` (nur Chat, kein Banner) |
| Verifikation | RCON `list` erreichbar | HTTPS API `HealthCheck` + `QueryServerState` |
| Countdown-Abbruch | `/mc modpack cancel` + `!cancel` | `/sat update cancel` |

### Neue/Geänderte Dateien für SAT
| Datei | Änderung |
|-------|-----------|
| `modules/monitoring/update_checker.py` | Countdown-Integration, Backup-Hook, Health-Suppress |
| `cogs/scheduler_cog.py` | SAT in gleichen Zeitplan einbinden |
| `cogs/satisfactory_cog.py` | `/sat update cancel` Befehl |

---

## 17. Server-Setup (Voraussetzungen — einmalig per SSH)

### Kritisch: Ohne diese Schritte funktioniert das Update-System NICHT!

#### 1. Sudoers-Konfiguration für botuser
```bash
# Als root auf dem Server:
cat > /etc/sudoers.d/botuser-updates << 'EOF'
# Discord Bot — Update-System File-Operationen

# Staging → Server-Ordner (Atomic Swap: neue Dateien einspielen)
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/.update_staging/* /home/minecraft/*
botuser ALL=(minecraft) NOPASSWD: /bin/cp -r /home/minecraft/.update_staging/* /home/minecraft/*

# Rollback-Operationen (mods/config sichern + wiederherstellen)
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/mods /home/minecraft/bmc5/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/config /home/minecraft/bmc5/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/mods_rollback_* /home/minecraft/bmc5/mods
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/bmc5/config_rollback_* /home/minecraft/bmc5/config
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/mods /home/minecraft/vanilla/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/config /home/minecraft/vanilla/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/mods_rollback_* /home/minecraft/vanilla/mods
botuser ALL=(minecraft) NOPASSWD: /bin/mv /home/minecraft/vanilla/config_rollback_* /home/minecraft/vanilla/config

# Alte Rollback-Ordner aufräumen (Retention: 2 Versionen)
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/bmc5/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/bmc5/config_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/vanilla/mods_rollback_*
botuser ALL=(minecraft) NOPASSWD: /bin/rm -rf /home/minecraft/vanilla/config_rollback_*

# Ownership nach Atomic Swap setzen
botuser ALL=(root) NOPASSWD: /bin/chown -R minecraft\:minecraft /home/minecraft/bmc5/*
botuser ALL=(root) NOPASSWD: /bin/chown -R minecraft\:minecraft /home/minecraft/vanilla/*
EOF
chmod 440 /etc/sudoers.d/botuser-updates
visudo -cf /etc/sudoers.d/botuser-updates  # Syntax-Check!
```

#### 2. Staging-Verzeichnis erstellen
```bash
mkdir -p /home/minecraft/.update_staging
chown botuser:minecraft /home/minecraft/.update_staging
chmod 775 /home/minecraft/.update_staging
```

#### 3. ENV-Variablen in .env setzen
```bash
# In /home/botuser/Discord_Bots/config/.env ergänzen:
CURSEFORGE_API_KEY=$2a$10$...
MC_BMC_CURSEFORGE_PROJECT_ID=462042
MC_BMC_CURSEFORGE_FILE_ID=7449464
MC_BMC_MODPACK_VERSION=v47
BOT_OWNER_ID=1000000000000000001
```

#### 4. Test der Berechtigungen
```bash
# Staging-Verzeichnis testen:
sudo -u botuser touch /home/minecraft/.update_staging/test.txt
sudo -u botuser rm /home/minecraft/.update_staging/test.txt
echo "Staging-Verzeichnis OK"

# Rollback-mv testen (erstellt temp-Ordner, verschiebt, räumt auf):
sudo -u botuser mkdir -p /home/minecraft/.update_staging/test_rollback
sudo -u botuser sudo -u minecraft mv /home/minecraft/.update_staging/test_rollback /home/minecraft/bmc5/mods_rollback_test
sudo -u botuser sudo -u minecraft mv /home/minecraft/bmc5/mods_rollback_test /home/minecraft/.update_staging/
sudo -u botuser rm -rf /home/minecraft/.update_staging/test_rollback
echo "Rollback-Operationen OK"

# chown testen:
sudo -u botuser sudo chown -R minecraft:minecraft /home/minecraft/.update_staging/
echo "Chown OK"

# systemctl-Befehle testen (sollten bereits funktionieren):
sudo -u botuser sudo systemctl status minecraft-bmc
echo "systemctl OK"
```

---

## Änderungshistorie

| Version | Datum | Änderungen |
|---------|-------|-------------|
| 1.0 | 13.03.2026 | Initialer Plan |
| 1.1 | 13.03.2026 | Versions-Vergleich auf Modrinth ID umgestellt (v31≠v47 Fix), In-Game-Befehle detailliert mit Code-Beispielen und Architektur, Backup-Integration explizit an bestehende Module gebunden (MinecraftBackupManager + OneDriveBackup), DM-an-Owner als neue Implementierung dokumentiert, Voice-Channel Format "X/Y", NeoForge Join/Leave Fallback, Countdown um 2min und 30sek erweitert, BOT_OWNER_ID ENV-Variable, Satisfactory Check-Only Section |
| 1.2 | 13.03.2026 | SAT-Updater auf vollständiges Auto-Update umgestellt (nicht nur Check), In-Game-Befehle erweitert (!tps, !restart, !backup), SpielerName420 als Default-OP, Backup-Retention auf 2 Versionen umgestellt (statt 7 Tage), NeoForge Spielererkennung dokumentiert (Dual-Erkennung: Log + RCON-Polling), Offene Entscheidungen mit Marcos Antworten aktualisiert (Channel-IDs, Rollen-ID, Owner-ID), ENV-Variablen mit konkreten Werten |
| 1.3 | 13.03.2026 | **KRITISCH: Komplett auf CurseForge API umgestellt** — Modrinth hat kein Server Pack (nur .mrpack Client), CurseForge hat Server Pack mit direkter Verlinkung via `serverPackFileId`. Versionsvergleich via CurseForge File-ID statt Modrinth Version-ID. API-Flow verifiziert mit echten Daten (v47→v48.5). CurseForge API-Key eingerichtet. ENV-Variablen auf CurseForge-Schema umgestellt. |
| 1.4 | 13.03.2026 | **Architektur + Analyse-Ergebnisse:** UpdateManager Wrapper-Klasse als zentraler Orchestrator. MCCountdownTimer erweitert RestartTimer für MC RCON /title Banner. Neue Module: file_manager.py (Download, ZIP, Hash, Atomic Swap), mc_countdown.py. **Optimierungen:** RestartTimer wiederverwenden, Version-Tracking in SQLite statt .env, HAR-Suppress VOR Countdown, SAT perform_update() vervollständigt. **Verbesserungen:** Disk-Space Pre-Flight, SHA1/MD5 Hash-Verifikation, Atomic Swap via Staging, Concurrent-Lock (asyncio.Lock pro Server), CurseForge Rate-Limiter (Exponential Backoff), Crash-Recovery (abgebrochenes Update fortsetzen mit 3-Versuche-System). DB-Schema: `server_versions` Tabelle für Version-Tracking, `modpack_updates` um `update_phase` und `download_hash_sha1` erweitert. |
| 1.5 | 15.03.2026 | **Finalisierung:** Doppelte §4-Nummerierung behoben (CurseForge API → §5, alle Folgesektionen +1 → 17 Abschnitte total). Modrinth-Referenz in §8 Beispiel-Embed korrigiert → CurseForge. §16 SAT perform_update() als bereits implementiert markiert. §10 Dateiliste um Implementierungs-Status ergänzt (7 Module fertig lokal, 9 Integrationen offen). §14 offene Entscheidungen aktualisiert (ENV-Werte bekannt, Deployment nötig). Status von PLANUNG auf IMPLEMENTIERUNG geändert. |
| 1.6 | 15.03.2026 | **Qualitäts-Review:** §17 Sudoers erweitert um Rollback-Operationen + Retention-Cleanup + visudo Syntax-Check + erweiterte Berechtigungstests. §16 falscher Methodenname korrigiert (`_safe_start()` ist Start, nicht Stop). Phase 7 Timeout-Diskrepanz dokumentiert (120s für MC, 90s für SAT). §3 Zeitplan: 00:00-Check-Flag über `modpack_updates.status='scheduled'` gelöst + neuer DB-Status. Phase 3 Server-Stop präzisiert (RCON stop → systemctl-Polling → systemctl stop als Fallback). Phase 6 Download-Optimierungen: Streaming-Hash (SHA1 während Download) + HTTP Range Requests für Download-Resume. §9 Retention um Cleanup-Mechanismus ergänzt (via scheduler_cog.py). Phase 0 `update_phase` Werte vollständig aufgelistet (7 Phasen). MCCountdownTimer api=None Sicherheitskommentar. |
