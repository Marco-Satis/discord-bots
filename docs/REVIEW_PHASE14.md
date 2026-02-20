# Code-Review Phase 14: Command-Aufraeumung

| Feld          | Wert                                       |
|---------------|---------------------------------------------|
| **Datum**     | 2026-02-20                                  |
| **Reviewer**  | Claude Opus 4.6 (automatisiert)             |
| **Scope**     | 5 Cog-Dateien nach Phase 14 Migration       |
| **Version**   | v3.2.0-pre (F25)                            |

---

## Zusammenfassung

Phase 14 hat Server-Steuerungsbefehle (start/stop/restart/cancel) und Admin-Config-
Commands ins Web-Dashboard migriert. Die verbleibenden Discord-Commands wurden auf
Lese- und Spieler-orientierte Funktionen reduziert.

**Gesamt-Verdict: PASS mit Anmerkungen (3 Minor Issues, 0 Critical)**

---

## Checkpoint 1: Imports

### satisfactory_cog.py
- **Zeile 21** — `from typing import Optional`: **UNUSED**. `Optional` wird nirgendwo
  im File als Typ-Annotation verwendet (alle Parameter nutzen `= None` ohne
  `Optional[...]` Wrapper). Seit Python 3.10 kann `X | None` verwendet werden,
  aber hier fehlt jegliche Verwendung komplett.
- Alle anderen Imports (`asyncio`, `discord`, `app_commands`, `commands`, `Path`,
  `get_logger`, `format_uptime`, `format_bytes`, `status_emoji`,
  `admin_only`, `spieler_only`, `owner_only`, `is_admin`,
  `server_online_required`, `TimerResult`) werden verwendet.

### minecraft_cog.py
- Alle Imports werden verwendet: `asyncio`, `json`, `re`, `tempfile`, `zipfile`,
  `datetime`, `Path`, `discord`, `app_commands`, `commands`, `Optional`, `Dict`,
  `get_logger`, `format_uptime`, `format_bytes`, `status_emoji`, `DATA_DIR`,
  `admin_only`, `spieler_only`, `owner_only`, `MinecraftServer`,
  `MinecraftBackupManager`, `MinecraftBlacklist`, `WorldAnalyzer`.
- **Keine Issues.**

### general_cog.py
- Alle Imports werden verwendet.
- **Keine Issues.**

### mod_cog.py
- Alle Imports werden verwendet (`Path`, `Optional`, `spieler_only`, `truncate`,
  `ModManager`).
- **Keine Issues.**

### maintenance_cog.py
- Nur `commands` und `get_logger` importiert — beide verwendet.
- **Keine Issues.**

| Datei              | Verdict |
|--------------------|---------|
| satisfactory_cog   | MINOR: `Optional` unused |
| minecraft_cog      | PASS    |
| general_cog        | PASS    |
| mod_cog            | PASS    |
| maintenance_cog    | PASS    |

---

## Checkpoint 2: Type-Hints

- **satisfactory_cog.py**: Konsistent. `bot: commands.Bot`, `interaction: discord.Interaction`
  durchgehend verwendet. Kein gebrochener Typ-Verweis.
- **minecraft_cog.py**: Konsistent. `Dict[str, MinecraftServer]`, `Optional[str]`,
  `list[app_commands.Choice[str]]` durchgehend korrekt.
- **general_cog.py**: Konsistent. `Optional[int]`, `Optional[str]`,
  `Optional[datetime]`, `dict[int, dict]` korrekt verwendet.
- **mod_cog.py**: Konsistent. `Optional[str]` verwendet.
- **maintenance_cog.py**: Minimal, aber korrekt (`bot: commands.Bot`).

**Verdict: PASS (alle Dateien)**

---

## Checkpoint 3: Exception Handling

### satisfactory_cog.py
- **Kein bare `except:`** — alle Exception-Handler sind typisiert
  (`Exception as e`, `discord.HTTPException`, `app_commands.CheckFailure`).
- **Zeile 107-108**: `except Exception as e` mit `logger.debug()` — korrekt,
  API-Nichtverfuegbarkeit ist erwartbar.
- **Zeile 177-178**: `except Exception as e` mit `f"Fehler: {e}"` an User —
  **MINOR**: Interne Fehlermeldung wird an User geleakt. Empfehlung:
  Generische Meldung an User, Details nur ins Log.
- **Zeile 1113**: `except Exception: pass` im Error-Handler — akzeptabel als
  letzter Fallback um Endlos-Rekursion zu vermeiden.

### minecraft_cog.py
- Durchgehend sauberes Exception-Handling.
- `except asyncio.CancelledError: pass` (Zeile 242, 908) — korrekt.
- Error-Handler (Zeile 1664-1686) mit `except Exception: pass` als Fallback.

### general_cog.py
- `except Exception: pass` nur im Error-Handler (Zeile 770) und bei
  Fortschritts-Updates (Zeile 291, 341, 362) — akzeptabel.
- `except discord.Forbidden`, `except discord.NotFound` korrekt behandelt.

### mod_cog.py
- Sauber. Error-Handler gibt generische Meldung zurueck (Zeile 186:
  `"Ein Fehler ist aufgetreten."`), keine internen Details an User.

### maintenance_cog.py
- Kein Exception-Handling noetig (keine Commands).

**Verdict: PASS (alle Dateien, 1 Minor in satisfactory_cog.py Zeile 178)**

---

## Checkpoint 4: Async/Await

- **satisfactory_cog.py**: Alle `async def` Methoden verwenden `await` korrekt.
  `interaction.response.defer()`, `interaction.followup.send()`,
  `self.api.save_game()`, `self.server.get_status()` — alle awaited.
- **minecraft_cog.py**: Alle async-Aufrufe korrekt awaited. `asyncio.create_task()`
  fuer Background-Tasks (Announcements, Autosave) korrekt verwendet.
  `asyncio.sleep()` statt `time.sleep()`.
- **general_cog.py**: `asyncio.create_task()` fuer Clear-Resume korrekt.
  `channel.history()`, `channel.delete_messages()`, `msg.delete()` alle awaited.
- **mod_cog.py**: Einfach. `interaction.response.defer()` und
  `interaction.followup.send()` korrekt awaited.
- **maintenance_cog.py**: Keine async-Operationen.

**Verdict: PASS (alle Dateien)**

---

## Checkpoint 5: Security

### satisfactory_cog.py
- Kein User-Input wird direkt in Shell-Commands oder SQL verwendet.
- **Zeile 1206-1209** (LoadConfirmView): `re.sub(r'[^\w\-]', '', self.savename)`
  sanitisiert den Savegame-Namen vor RCON-Aufruf. **Korrekt.**
- Blueprint-Uploads validieren Dateitypen (Zeile 684-689, 733-739).
- `discord.utils.escape_mentions()` wird nicht fuer alle User-Inputs in Embeds
  verwendet, aber Embeds sind gegen Mentions immun (Discord rendert keine
  @mentions in Embed-Feldern). **Akzeptabel.**

### minecraft_cog.py
- **`_sanitize_rcon_input()`** (Zeile 39-42): Sanitisiert User-Input fuer
  RCON-Befehle mit Regex `[^\w\s\-]`. Max-Length-Limit. **Korrekt.**
- Wird konsequent bei allen RCON-Befehlen verwendet: kick, ban, pardon,
  whitelist add/remove, blacklist add/remove.
- `discord.utils.escape_mentions()` wird in Blacklist-Embeds verwendet
  (Zeile 1344-1345, 1394, 1426-1427, 1464, 1473, 1493). **Gut.**

### general_cog.py
- `/clear` erfordert `@admin_only()`. Kein Injektionsrisiko.
- Datums-Parsing (Zeile 725-733) verwendet `strptime()` — kein Risiko.

### mod_cog.py
- `truncate()` begrenzt Ausgabe-Laengen. Keine Injektionsrisiken.

### maintenance_cog.py
- Keine Commands, kein Risiko.

**Verdict: PASS (alle Dateien)**

---

## Checkpoint 6: Dead References (tote Verweise auf geloeschte Befehle)

### satisfactory_cog.py
- **Zeile 433**: `"Nutze /sat stop zuerst."` — Verweis auf geloeschten Command
  `/sat stop`. Dieser Befehl existiert nicht mehr als Discord-Command
  (ins Dashboard migriert). **DEAD REFERENCE.**
- **Zeile 1157**: `"Starte den Server mit /sat start"` — Verweis auf geloeschten
  Command `/sat start`. **DEAD REFERENCE.**
- **Zeile 1275**: `"Nutze /sat cancel zuerst."` — Verweis auf geloeschten
  Command `/sat cancel`. **DEAD REFERENCE.**

### general_cog.py (_HELP_COMMANDS)
- **Zeile 527**: `("/mc config update [server]", "Paper-Update pruefen", _LEVEL_OWNER)` —
  `/mc config update` wurde ins Dashboard migriert. **DEAD REFERENCE in Help-Menu.**
- `/mc backup create` fehlt in der Help-Liste — existierender Command wird
  nicht angezeigt. **FEHLENDER EINTRAG.**
- `/mc world stats` fehlt in der Help-Liste. **FEHLENDER EINTRAG.**

### scheduler_cog.py (ausserhalb Scope, aber relevant)
- **Zeile 892**: `"Verwende /mc config update zum Installieren."` — Verweis auf
  geloeschten Command. **DEAD REFERENCE** (nicht im Review-Scope, aber notiert).

### minecraft_cog.py, mod_cog.py, maintenance_cog.py
- **Keine Dead References.**

| Datei              | Dead References                        |
|--------------------|----------------------------------------|
| satisfactory_cog   | 3x (Zeilen 433, 1157, 1275)           |
| general_cog        | 1x (Zeile 527) + 2 fehlende Eintraege |
| minecraft_cog      | Keine                                  |
| mod_cog            | Keine                                  |
| maintenance_cog    | Keine                                  |

---

## Checkpoint 7: Edge Cases

- **Satisfactory Restore (Zeile 432-433)**: Sagt `"Nutze /sat stop zuerst"` —
  funktional kein Problem, da der Server-Status-Check korrekt funktioniert.
  Nur der Hinweistext ist veraltet (sollte aufs Dashboard verweisen).
- **Blueprint Restart View (Zeile 1273)**: Funktioniert weiterhin korrekt,
  da `timer_mgr` unabhaengig von den entfernten Commands arbeitet.
  Der Verweis auf `/sat cancel` im Fehlertext ist nur kosmetisch falsch.
- **Autosave-System in MC-Cog**: Bleibt funktional, da `_autosave_loop`
  und `_load_autosave_config` unabhaengig von geloeschten Commands sind.
  Config-Datei `mc_autosave.json` wird weiterhin gelesen.
- **ModCog**: `mod_list` hat keine `@spieler_only()` Dekoration (Zeile 48-49),
  waehrend `mod_info` sie hat (Zeile 112). Koennte gewollt sein
  (Liste fuer alle, Details fuer Spieler), aber ist inkonsistent mit
  der Help-Tabelle die beide als `_LEVEL_SPIELER` markiert.
- **MaintenanceCog**: Leerer Cog wird weiterhin geladen. Kein Problem,
  aber koennte entfernt werden wenn nicht geplant ist, neue Commands
  hinzuzufuegen.

**Verdict: PASS (funktional korrekt, kosmetische Issues)**

---

## Per-File Verdicts

### 1. satisfactory_cog.py
| Checkpoint         | Status    | Details                                    |
|--------------------|-----------|--------------------------------------------|
| Imports            | MINOR     | `Optional` (Zeile 21) unused               |
| Type-Hints         | PASS      |                                             |
| Exception Handling | MINOR     | Zeile 178: Interner Fehler an User geleakt  |
| Async/Await        | PASS      |                                             |
| Security           | PASS      |                                             |
| Dead References    | NEEDS FIX | 3x Verweise auf /sat start, stop, cancel   |
| Edge Cases         | PASS      |                                             |
| **Gesamt**         | **NEEDS FIX** | Dead References muessen aktualisiert werden |

### 2. minecraft_cog.py
| Checkpoint         | Status | Details |
|--------------------|--------|---------|
| Imports            | PASS   |         |
| Type-Hints         | PASS   |         |
| Exception Handling | PASS   |         |
| Async/Await        | PASS   |         |
| Security           | PASS   |         |
| Dead References    | PASS   |         |
| Edge Cases         | PASS   |         |
| **Gesamt**         | **PASS** |       |

### 3. general_cog.py
| Checkpoint         | Status    | Details                                        |
|--------------------|-----------|------------------------------------------------|
| Imports            | PASS      |                                                |
| Type-Hints         | PASS      |                                                |
| Exception Handling | PASS      |                                                |
| Async/Await        | PASS      |                                                |
| Security           | PASS      |                                                |
| Dead References    | NEEDS FIX | /mc config update in _HELP_COMMANDS (Zeile 527)|
| Edge Cases         | MINOR     | /mc backup create + /mc world stats fehlen     |
| **Gesamt**         | **NEEDS FIX** | Help-Menu muss aktualisiert werden         |

### 4. mod_cog.py
| Checkpoint         | Status | Details                                           |
|--------------------|--------|---------------------------------------------------|
| Imports            | PASS   |                                                   |
| Type-Hints         | PASS   |                                                   |
| Exception Handling | PASS   |                                                   |
| Async/Await        | PASS   |                                                   |
| Security           | PASS   |                                                   |
| Dead References    | PASS   |                                                   |
| Edge Cases         | MINOR  | mod_list fehlt @spieler_only (inkonsistent mit Help) |
| **Gesamt**         | **PASS** |                                                 |

### 5. maintenance_cog.py
| Checkpoint         | Status | Details                                    |
|--------------------|--------|--------------------------------------------|
| Imports            | PASS   |                                            |
| Type-Hints         | PASS   |                                            |
| Exception Handling | PASS   | Kein Code vorhanden                        |
| Async/Await        | PASS   | Kein Code vorhanden                        |
| Security           | PASS   |                                            |
| Dead References    | PASS   |                                            |
| Edge Cases         | PASS   | Leerer Cog, koennte spaeter entfernt werden|
| **Gesamt**         | **PASS** |                                          |

---

## Gefundene Issues (Zusammenfassung)

### NEEDS FIX (empfohlen vor Release)

| #  | Datei              | Zeile | Beschreibung                                     | Schwere |
|----|--------------------|-------|--------------------------------------------------|---------|
| F1 | satisfactory_cog   | 433   | Verweis auf `/sat stop` (geloescht)              | Minor   |
| F2 | satisfactory_cog   | 1157  | Verweis auf `/sat start` (geloescht)             | Minor   |
| F3 | satisfactory_cog   | 1275  | Verweis auf `/sat cancel` (geloescht)            | Minor   |
| F4 | general_cog        | 527   | `/mc config update` in Help (geloescht)          | Minor   |

### EMPFEHLUNGEN (optional)

| #  | Datei              | Zeile | Beschreibung                                     |
|----|--------------------|-------|--------------------------------------------------|
| E1 | satisfactory_cog   | 21    | Unused import `Optional` entfernen               |
| E2 | general_cog        | 520-524 | `/mc backup create` in Help ergaenzen          |
| E3 | general_cog        | —     | `/mc world stats` in Help ergaenzen              |
| E4 | mod_cog            | 48    | `@spieler_only()` bei `mod_list` ergaenzen       |
| E5 | scheduler_cog      | 892   | Verweis auf `/mc config update` aktualisieren    |

---

## Angewendete Fixes

Keine Fixes angewendet — nur Review-Dokumentation erstellt.
Die oben aufgelisteten Issues sollten vor dem v3.2.0 Release behoben werden.

### Empfohlene Aenderungen:

**F1-F3 (satisfactory_cog.py):**
- Zeile 433: `"Nutze /sat stop zuerst."` aendern zu
  `"Stoppe den Server zuerst ueber das Dashboard."`
- Zeile 1157: `"Starte den Server mit /sat start"` aendern zu
  `"Starte den Server ueber das Dashboard."`
- Zeile 1275: `"Nutze /sat cancel zuerst."` aendern zu
  `"Brich den aktiven Timer zuerst ueber das Dashboard ab."`

**F4 (general_cog.py):**
- Zeile 527: Eintrag `("/mc config update [server]", ...)` entfernen.
- Neue Eintraege ergaenzen:
  - `("/mc backup create [server]", "Backup erstellen", _LEVEL_SPIELER)`
  - `("/mc world stats [server]", "Detaillierte Welt-Analyse", _LEVEL_ALL)`

---

## Gesamt-Verdict

**PASS MIT ANMERKUNGEN**

Die Phase-14-Migration ist sauber durchgefuehrt. Alle Server-Steuerungsbefehle
wurden korrekt entfernt. Die verbleibenden Commands funktionieren unabhaengig
von den geloeschten. Es gibt **keine kritischen Fehler** und **keine Security-Issues**.

Die 4 gefundenen Minor-Issues (tote Textverweise) haben keinen Einfluss auf
die Funktionalitaet, sollten aber vor dem Release korrigiert werden, um
User-Verwirrung zu vermeiden.
