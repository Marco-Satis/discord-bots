# Temp-Voice Upgrade-Plan — „Join2Create" auf Maki/COD-Warzone-Niveau

> Ziel: das bestehende Temp-Voice-System (`cogs/temp_voice_cog.py`, `modules/temp_voice.py`, `modules/temp_voice_views.py`) von 4 auf ~12 Controls heben, UX wie bei großen Servern (Maki / DE-COD-Warzone). Phasenweise deploybar.

## Ist-Zustand (was schon da ist) ✅
- **Join2Create**: rein joinen → eigener Temp-Channel wird erstellt.
- **Persistentes Control-Panel** (Buttons überleben Bot-Neustart via `custom_id`).
- **4 Controls**: Umbenennen (Modal), Userlimit (Modal), Sperren/Entsperren, Owner übertragen.
- **Auto-Cleanup**: leerer Channel wird nach AFK-Timeout gelöscht; Startup-Cleanup verwaister Channels.
- **Config**: Join-Channel, Kategorie, Default-Limit, AFK-Timeout (`/tempvoice setup`).

## Soll-Zustand (Maki-Tier) — fehlende Features
| Control | Was es macht | Priorität |
|---|---|---|
| 👁️ **Verstecken/Zeigen** | Channel für @everyone unsichtbar (`view_channel`) — nur erlaubte sehen ihn | HOCH |
| ✅ **User erlauben (Permit)** | User-Select → gibt einem User connect+view trotz Lock/Hide | HOCH |
| ⛔ **User kicken/blocken (Reject)** | User-Select → trennt + sperrt User dauerhaft (persistent) | HOCH |
| 👑 **Claim** | Owner ist weg → ein anwesendes Mitglied übernimmt | HOCH |
| 🔢 **Limit** | ✅ vorhanden | — |
| ✏️ **Umbenennen** | ✅ vorhanden (⚠️ Discord-Limit 2×/10min, siehe Risiken) | — |
| 🔒 **Sperren** | ✅ vorhanden | — |
| 🤝 **Owner übertragen** | ✅ vorhanden | — |
| 🔉 **Bitrate** | Select (Stufen je nach Boost-Level) | MITTEL |
| 🌍 **Region** | Select (auto / EU / US …) gegen Lag | MITTEL |
| 🔗 **Invite** | Button generiert Invite-Link zum Channel | MITTEL |
| ℹ️ **Status im Embed** | Embed zeigt live: Owner, 🔒/👁️-Status, Limit, erlaubte/geblockte User | MITTEL |
| 📜 **/voice Fallback-Commands** | gleiche Aktionen als Slash-Commands (`/voice lock`, `/voice permit @u` …) | NIEDRIG |
| 🖥️ **Interface-Channel-Modus** | fester Text-Kanal mit Dauer-Panel das auf „deinen aktuellen" Voice wirkt (Maki-Style) | NIEDRIG/optional |

## Architektur-Änderungen

### Daten-Modell (`modules/temp_voice.py`)
Pro Channel zusätzlich speichern (atomic write, wie bisher):
```
_channels[cid] = {
    "owner_id": int,
    "name": str,
    "user_limit": int,
    "locked": bool,        # NEU
    "hidden": bool,        # NEU
    "bitrate": int,        # NEU
    "region": str | None,  # NEU
    "allowed": [user_id],  # NEU — Permit-Liste
    "blocked": [user_id],  # NEU — Reject/Ban-Liste (persistent re-applied)
}
```
Zentrale Permission-Helper (statt verstreut in Buttons):
- `apply_lock(channel, locked)` → `@everyone` connect allow/deny
- `apply_hidden(channel, hidden)` → `@everyone` view_channel allow/deny
- `permit_user(channel, user)` → overwrite connect+view=True, aus `blocked` entfernen
- `reject_user(channel, user)` → overwrite connect=False, disconnect falls drin, in `blocked`
- Beim **Channel-Create**: `blocked`-Liste als Overwrites re-applien (Ban überlebt Rejoin/Neustart).

### UI (`modules/temp_voice_views.py`)
- Panel-Embed redesignen: Titel + Status-Zeile (Owner, 🔒/👁️/Limit) + erlaubte/geblockte als Felder.
- Button-Layout (Discord: max 5/Reihe, 5 Reihen):
  - **Reihe 1:** ✏️ Umbenennen · 🔢 Limit · 🔒 Sperren · 👁️ Verstecken · 👑 Claim
  - **Reihe 2:** ✅ Erlauben · ⛔ Blocken · 🤝 Transfer · 🔗 Invite
  - **Reihe 3:** Select „Bitrate" · Select „Region" (Selects brauchen je eigene Reihe → ggf. in ein „⚙️ Mehr"-Untermenü auslagern)
- `UserSelect` (discord.ui.UserSelect) für Permit/Reject/Transfer/Claim statt ID-Eingabe.
- Alle Antworten **ephemeral**; nur Owner (oder Admin) darf Buttons nutzen → Check in `interaction_check`.

### Cog (`cogs/temp_voice_cog.py`)
- `_send_control_panel`: neues Embed + neue View.
- `on_voice_state_update`: bei Owner-Leave **nicht** sofort Owner-Transfer erzwingen → Claim ermöglichen (Owner-weg-Flag), Channel erst bei *komplett leer* + AFK-Timeout löschen.
- Persistente View beim `cog_load` neu registrieren (custom_ids stabil halten — sonst tote Buttons nach Deploy).

## Phasen (jede einzeln testbar + deploybar)
| Phase | Inhalt | Aufwand |
|---|---|---|
| **1** | Daten-Modell erweitern + zentrale Permission-Helper + `blocked`-Re-Apply bei Create | 1–2 h |
| **2** | Buttons: Verstecken, Erlauben (UserSelect), Blocken (UserSelect), Claim | 2 h |
| **3** | Bitrate- + Region-Select, Invite-Button | 1–2 h |
| **4** | Embed-Redesign mit Live-Status + Polish | 1 h |
| **5** | `/voice`-Fallback-Commands (optional) | 1 h |
| **6** | Interface-Channel-Modus (optional, Maki-Style) | 2 h |
| — | Tests (`tests/test_temp_voice.py`: Permission-Logik, blocked-Re-Apply, Claim) + Deploy | 1 h |

**Kern (Phase 1–4) ≈ 5–7 h → bringt 90 % des Maki-Gefühls.** Phase 5–6 optional.

## Risiken / Fallstricke (wichtig)
- **Rename-Rate-Limit:** Discord erlaubt nur **2 Kanal-Umbenennungen pro 10 Min pro Kanal**. Maki blockt/warnt deshalb. → Bei 429 freundliche Meldung „Zu oft umbenannt, warte X Min" statt Fehler. Bitrate/Limit/Region-Edits sind davon **nicht** betroffen.
- **Permission-Overwrite-Races:** schnelle Button-Klicks → `asyncio.Lock()` pro Channel.
- **Persistente Views nach Deploy:** `custom_id`s **stabil** lassen, sonst sind alte Panels tot. Beim Ändern: alte Panels neu posten oder Migrations-Cleanup.
- **Bitrate-Cap:** `guild.bitrate_limit` (Boost-abhängig) als Obergrenze prüfen, sonst `HTTPException`.
- **Hidden + Bot-Zugriff:** Bot-Rolle muss `view_channel` behalten, sonst verliert der Bot den Channel aus den Augen.
- **Ban-Persistenz:** `blocked`-Liste muss bei jedem Channel-Create (gleicher Owner) als Overwrite neu gesetzt werden — sonst umgeht ein User den Ban durch Rejoin.

## Referenz-Verhalten (Maki / DE-COD-Warzone)
- 1 zentrales Panel, Emoji-Buttons, alles ephemeral.
- Lock + Hide getrennt (gesperrt = sichtbar aber kein Beitritt; versteckt = unsichtbar).
- Permit/Reject per User-Auswahl.
- Claim wenn Owner weg.
- Auto-Delete bei leer.

→ Deckt sich 1:1 mit obigem Plan. Empfehlung: **Phase 1–4 umsetzen**, 5–6 später bei Bedarf.
