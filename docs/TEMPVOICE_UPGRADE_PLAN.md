# Temp-Voice Upgrade — FINALE Spec (VOICEPANEL-Style)

> Vorbild: VOICEPANEL V4.6 (Screenshot). Funktionen/Aufbau übernehmen, Design eigen. Baut auf bestehendem System auf (`cogs/temp_voice_cog.py`, `modules/temp_voice.py`, `modules/temp_voice_views.py`).

## Gelockte Entscheidungen (mit Marco abgestimmt)
- **Panel:** im Temp-Channel **+** fester Interface-Textkanal (beide Oberflächen, gleiche Aktionen).
- **PRIVATE/PUBLIC binär** (statt getrennt Lock+Hide): privat = zu + unsichtbar · öffentlich = offen + sichtbar.
- **BAN-Scope: pro Channel** (Ban weg wenn Channel gelöscht) + expliziter UNBAN.
- **Slot-Grid: erst Text-Liste**, Pillow-Bild später.
- **Activision-IDs: jetzt mitbauen** (braucht Verlink-Vorsystem).
- **Default: offen.** Name `🎮 {User}'s Voice`. **Claim** wenn Owner weg (manuell).

## Panel-Funktionen (final)
| # | Funktion | Verhalten | Status |
|---|---|---|---|
| 1 | 🎙️ RENAME | Name ändern (Modal) | ✅ vorhanden |
| 2 | 👤 LIMIT | Userlimit 0-99 (Modal) | ✅ vorhanden |
| 3 | 🔒 PRIVATE | Channel zu + unsichtbar für @everyone | neu |
| 4 | 🔓 PUBLIC | offen + sichtbar | neu |
| 5 | 🔄 TRANSFER | Owner an anderen übergeben (UserSelect) | ✅ vorhanden |
| 6 | 🚫 BAN | User trennen + connect-deny + in `banned` (pro Channel) | neu |
| 7 | 👤 UNBAN | aus `banned` entfernen (Select aus Ban-Liste) | neu |
| 8 | 👑 CLAIM | Owner weg → Anwesender übernimmt | neu |
| 9 | 📊 LOGS | letzte 8 Join/Leave-Events (ephemeral) | neu |
| 10 | 🎮 ACTIVISION IDs | verlinkte Activision-IDs der Anwesenden (ephemeral) | neu (+Vorsystem) |
| — | 👥 Slot-Liste | Mitglieder im Embed: Avatar/Name, 👑 Owner, Zeit-im-Channel | neu (Text-v1) |

## Daten-Modell
**Channel-State** (`modules/temp_voice.py`, JSON, ephemeral — Channels werden bei Restart eh aufgeräumt):
```
_channels[cid] = {
  "owner_id": int,
  "name": str,
  "user_limit": int,
  "private": bool,            # NEU
  "banned": [user_id],        # NEU (pro Channel)
  "joined_at": {uid: ts},     # NEU — Zeit-im-Channel für Slot-Liste
  "events": [{uid, type, ts}],# NEU — Ringpuffer letzte ~10 Join/Leave
}
```
**Activision-IDs** (persistent → SQLite via Migration, NICHT JSON):
```
TABLE user_activision (user_id INTEGER PRIMARY KEY, activision_id TEXT, linked_at TEXT)
```

**Zentrale Helper** (statt Logik in Buttons):
`set_private(ch)` · `set_public(ch)` · `ban_user(ch,u)` (disconnect+deny+store) · `unban_user(ch,u)` · `claim(ch,u)` · `log_event(cid,uid,type)` · beim Create: `banned` re-applien (innerhalb Session).

## UI
- **Embed-Redesign:** Header + Slot-Liste (Mitglieder, 👑, Zeit) + Status-Zeile (🔒/🔓 · Limit · Ban-Anzahl).
- **Buttons** (Discord max 5/Reihe): Reihe1 Rename·Limit·Private·Public·Transfer · Reihe2 Ban·Unban·Claim·Logs·Activision. `UserSelect` für Ban/Unban/Transfer.
- **Owner-only** (`interaction_check`) + Admin-Override. Antworten **ephemeral**. Persistente `custom_id`s.

## Phasen (einzeln testbar + deploybar)
| Phase | Inhalt | Aufwand |
|---|---|---|
| **1** | Daten-Modell + Helper (private/public, ban/unban, claim, event-log, joined_at) | 2 h |
| **2** | Panel-Embed (Slot-Text-Liste + Status) + neue Buttons + UserSelects | 2-3 h |
| **3** | Activision: Migration + `/link-activision` `/unlink-activision` + Panel-Button | 2-3 h |
| **4** | Interface-Textkanal: Dauer-Panel, „aktueller Voice"-Routing (`/tempvoice setup` erweitern) | 2-3 h |
| **5** | Tests (`tests/test_temp_voice.py`: private/public-Perms, ban+reconnect-block, claim, event-cap, activision-validate) + Deploy | 1 h |
| **6** | *(später)* Pillow-Slot-Grid-Bild statt Text | 2-3 h |

**Empfohlene Bau-Reihenfolge:** Phase 1+2 zuerst → funktionierendes Panel → **du testest in Discord + gibst Feedback** → dann Phase 3 (Activision) + 4 (Interface). Voice-UX ist Gefühl, willst du sehen bevor wir weiterbauen.

## Risiken (im Code beachten)
- Rename-Limit Discord **2×/10min pro Channel** → bei 429 freundlich warnen.
- Overwrite-Races → `asyncio.Lock()` pro Channel.
- Persistente Views: `custom_id`s stabil; bestehende Panels nach Deploy ggf. neu posten (Migrations-Cleanup beim `cog_load`).
- `private` + Bot-Zugriff: Bot-Rolle behält `view_channel`.
- Activision-ID-Eingabe: Format validieren (Pattern), Markdown escapen, kein PII-Leak in Logs.
- `enforce-secure-profile`/Permissions: Bot braucht `Manage Channels` + `Move Members` für Ban-Disconnect.
