# Feature-Plan — Discord Bot System v3.x+

> **Stand:** 20. Februar 2026 | **Basis:** v3.1.0

---

## Priorisierung

| Prio | Bedeutung |
|------|-----------|
| P2 | Mittlerer Aufwand, sinnvolle Erweiterung — als naechstes umsetzbar |
| P3 | Groesseres Projekt, langfristig geplant |

---

## Offene Features

### 11. MC World-Analyse per Command

Neuer Befehl `/mc world stats [server]` fuer eine detaillierte Analyse der aktuellen Minecraft-Welt. Zeigt auf Wunsch ausfuehrliche Infos zum aktuellen Spielstand als Discord-Embed.

**Command-Ausgabe (Embed-Felder):**

Welt-Info (aus `level.dat` via nbtlib):
- Seed, Welt-Alter (Tage/Stunden), Spawn-Koordinaten
- Schwierigkeitsgrad, Spielmodus, Hardcore ja/nein
- MC-Datenversion (= Server-Version)

Spieler-Statistiken (aus `stats/*.json`):
- Pro Spieler: Spielzeit, Tode, Mob-Kills, abgebaute Bloecke (Top 5), gecraftete Items (Top 5)
- Gelaufene/geschwommene/geflogene Distanz (in km)
- Gesamtstatistik ueber alle Spieler

Advancements (aus `advancements/*.json`):
- Fortschritt in % pro Spieler
- Zuletzt freigeschaltetes Achievement

Welt-Groesse:
- Erkundete Flaeche (Region-Files zaehlen → Chunks × 256m²)
- Dateisystem-Groesse der World (Overworld, Nether, End separat)

**Aufwand:** ~4-5 Stunden
**Dateien:** Neues Modul `modules/minecraft/world_analyzer.py`, Erweiterung `cogs/minecraft_cog.py`
**Abhaengigkeiten:** `nbtlib` + `anvil-parser2` (pip install)
**Hinweis:** Alle Datei-Operationen muessen in `asyncio.to_thread()` laufen (blocking I/O)

### 13. Web-Dashboard (Interaktiv)

Vollstaendiges Admin-Dashboard zur Verwaltung aller Server ueber den Browser. Ausschliesslich fuer Admins und Owner — kein oeffentlicher Zugang fuer normale User. Echtzeit-Updates per WebSocket, Login-geschuetzt.

**Tech-Stack:** FastAPI (async) + HTMX (Live-Updates) + Jinja2 (Templates) + WebSocket (bidirektional)

**Seiten und Bereiche (6 Seiten):**

1. **Uebersicht** (Startseite nach Login)
   - Server-Kacheln pro Server: SAT + MC (BMC, Vanilla) + TS (spaeter)
   - Pro Kachel: Status, Spieleranzahl, Uptime, CPU/RAM, Quick-Actions (Start/Stop/Restart)
   - System-Performance: CPU, RAM, Disk, Netzwerk (Balken mit Farbcodierung)
   - Bot-Status-Leiste: Pro Bot (GameServer, Monitor, Admin) Online/Offline-Indikator + Discord-Latenz (Ping in ms, farbcodiert: gruen <100ms, gelb <300ms, rot >300ms)
   - Event-Feed: Letzte Ereignisse (Joins, Backups, Health Checks, Warnungen)
   - Klick auf Server-Kachel → oeffnet Server-Detailseite

2. **Server-Detail** (pro Server, erreichbar per Klick auf Kachel)
   - Fokus auf Bot/Game-spezifische Infos die Webmin nicht bietet:
   - **Spielerliste:** Aktuelle Spieler (Live-Update), Kick/Ban mit Grund-Eingabe
   - **RCON-Console:** Befehle direkt an den MC-Server senden (nur MC)
   - **Backups:** Backup-Liste mit Groesse/Zeitstempel, manuelles Backup ausloesen, Download, OneDrive-Status
   - **Savegame/World-Info:** SAT-Savegame-Details bzw. MC-World-Stats (Welt-Alter, Seed, Groesse)
   - **Config-Auszug:** Wichtigste Server-Settings auf einen Blick (server.properties / SAT-Config)
   - **Update-Status:** Aktuelle Version vs. verfuegbare Version (SteamCMD / Paper API / Modpack)
   - **Blacklist:** Eintraege anzeigen, hinzufuegen, aufheben (MC)
   - **Mod-Verwaltung:** Installierte Mods anzeigen, suchen, installieren, deinstallieren, updaten, Modpack export/import (Details siehe unten)
   - **Analyse & Statistiken** (Admin-Tool zur Server-Ueberwachung, Details siehe unten)
   - Start / Stop / Restart / Maintenance-Mode Buttons mit Bestaetigung

   **Analyse & Statistiken (Tab in Server-Detail):**
   Dedizierter Analyse-Bereich pro Server — nur fuer Admins/Owner zur Ueberwachung und Entscheidungsfindung.

   Alle Server:
   - **Uptime-Verlauf:** Diagramm (7/30 Tage), Verfuegbarkeit in %, Ausfall-Log mit Zeitstempel/Dauer/Grund
   - **Performance-Timeline:** CPU/RAM-Verlauf ueber Zeit (Stunden/Tage), Warnschwellen eingezeichnet
   - **Spieler-Aktivitaet:** Spieler-Online-Verlauf (Peak-Zeiten erkennen), Durchschnitt/Maximum pro Tag
   - **Backup-Statistiken:** Backup-Groesse ueber Zeit, Erfolgsrate, letzte Fehler

   Zusaetzlich fuer Minecraft-Server:
   - **Welt-Wachstum:** Region-Files ueber Zeit (erkundete Flaeche in km²), Dateisystem-Groesse Verlauf (Overworld/Nether/End)
   - **Spieler-Statistiken:** Top-Spieler nach Spielzeit, Tode, Mob-Kills, abgebaute Bloecke (Ranking-Tabelle)
   - **Advancement-Fortschritt:** Fortschritts-Balken pro Spieler (Prozent aller Advancements)
   - **TPS-Verlauf:** Server-Performance (Ticks-per-Second) ueber Zeit, Lag-Spikes markiert

   Zusaetzlich fuer Satisfactory-Server:
   - **Session-Daten:** Aktuelle Savegame-Details, letzte Savegame-Groesse, Spieldauer insgesamt
   - **Update-Historie:** SteamCMD Update-Verlauf, Version-Timeline

   Technisch:
   - Datenquelle: Bestehende JSON-Dateien (`data/monitor/`) + `modules/minecraft/world_analyzer.py` (Feature #11)
   - Diagramme: Chart.js (clientseitig) mit Daten per REST-API
   - Zeitraum-Auswahl: 24h, 7 Tage, 30 Tage (Toggle-Buttons)
   - Neuer Hintergrund-Task im Monitor Bot: `modules/monitoring/stats_collector.py` — sammelt regelmaessig (alle 5 Min) CPU/RAM/Spieleranzahl/TPS und schreibt in `data/monitor/stats_history.json` (Ringbuffer, max. 30 Tage)
   - Welt-Wachstum: Einmal taeglich per Cron-Task Regionfile-Count + Groesse loggen

   **Mod-Verwaltung (Tab in Server-Detail):**
   Dashboard-Version der bestehenden `/mod`-Commands — komfortabler als Discord, mit visueller Uebersicht.

   Installierte Mods (Tabelle):
   - Liste aller installierten Mods pro Server: Name, Version, installiert am, Beschreibung
   - Status-Spalte: Aktuell (gruen), Update verfuegbar (gelb), Inkompatibel (rot)
   - Aktionen pro Mod: Update-Button, Deinstallieren-Button, Info-Link (Modrinth/CurseForge)
   - Sortierbar nach Name, Installationsdatum, Status
   - Suchfeld zum Filtern der installierten Mods

   Mod-Suche + Installation:
   - Suchfeld mit Quelle (Modrinth / CurseForge / ficsit.app fuer SAT)
   - Suchergebnisse als Karten: Name, Beschreibung, Downloads, Version, Kompatibilitaet
   - Install-Button direkt in der Ergebnisliste (mit Versions-Dropdown)
   - Fortschrittsanzeige waehrend Installation

   Mod-Updates:
   - "Alle pruefen"-Button → zeigt alle Mods mit verfuegbaren Updates
   - Einzeln oder alle auf einmal aktualisieren
   - Changelog-Vorschau vor dem Update (wenn verfuegbar)
   - Warnung wenn Update Server-Neustart erfordert

   Modpack Export/Import:
   - Export: Aktuelle Mod-Liste als JSON exportieren (Download-Button)
   - Import: JSON hochladen → Mods automatisch installieren
   - Nützlich fuer Backup oder Uebertragung auf anderen Server

   Technisch:
   - Nutzt bestehenden `ModManager` (`modules/mod_manager.py`) — gleiche Logik wie Discord-Commands
   - REST-Endpunkte: `GET /api/server/{id}/mods`, `POST /api/server/{id}/mods/install`, `DELETE /api/server/{id}/mods/{name}`, etc.
   - WebSocket-Event `mod_install_progress` fuer Fortschrittsanzeige
   - Server muss fuer Install/Uninstall nicht zwingend gestoppt sein (abhaengig vom Mod-Typ, Warnung im UI)

3. **Fehler-Uebersicht** (minimaler Log-Viewer)
   - Kompakte Liste der letzten Fehler und Warnungen aller Bots
   - Gefiltert auf ERROR und WARNING Level
   - Zeitstempel, Bot-Name, Fehlermeldung
   - Keine vollstaendige Log-Suche (dafuer Webmin/journalctl nutzen)

4. **Admin Bot Setup** (vollstaendiger Maki-Ersatz)
   - Tab **Temp Voice:** Hub-Channel, Kategorie, Owner-Berechtigungen (Toggles), AFK-Handling
   - Tab **TeamSpeak:** ServerQuery-Verbindung, Chat-Bridge, Auto-Channels pro Gameserver
   - Tab **WordFilter:** Wortlisten (exakt, partial, regex) mit Tag-System
   - Tab **AntiSpam:** Rate-Limits, Massnahmen, Duplicate/Mention/Emoji-Schutz
   - Tab **Warn-System:** Stufenbasiert (Punkte → Massnahme), Verfall-Dauer, Log-Channel
   - Tab **Reaction Roles:** Embeds verwalten, Emoji-Rollen-Paare zuordnen
   - Tab **Leveling/XP:** XP-Raten, Cooldowns, Multiplikatoren pro Channel, Rollen-Rewards, Voice-XP
   - Tab **Tickets:** Button-Channel, Support-Rollen, Transcript-Einstellungen
   - Tab **Audit-Logging:** Log-Channels pro Kategorie (Mod, Join/Leave, Member, Role, Server)
   - Tab **Giveaways:** Aktive Giveaways verwalten, neue erstellen, Teilnahmebedingungen, Standard-Einstellungen

5. **Config-Panel** (= Feature #14, integriert)
   - `config.json` bearbeiten: Feature-Toggles, Intervalle, Schwellwerte
   - Benachrichtigungs-Routing: Matrix — welches Ereignis geht in welchen Channel (Admin/Spieler/Log/Mod/Aus) + E-Mail
   - Dashboard-Login-Verwaltung: Berechtigte Rollen + User-IDs, aktive Sessions, Notfall-Passwort
   - Bot-Profile: Anzeigenamen, Avatare und Status-Texte der 3 Bots aendern
   - Formular-basiert (kein JSON-Editor), gruppiert nach Kategorie
   - Aenderungen sofort anwenden (Hot-Reload der Bots)
   - Aenderungs-Historie (wer hat wann was geaendert)

6. **System (Webmin-Einbettung)**
   - Webmin (Port 9090) als iframe im Dashboard eingebettet
   - Vollstaendige Webmin-Oberflaeche nutzbar ohne separaten Browser-Tab
   - Zugang ueber Nginx Reverse-Proxy (gleiche Domain, Pfad `/webmin/`)
   - Hauptnutzung: System-Updates, Paketverwaltung, Server-Wartung, Logs

**Authentifizierung (Dual-Login):**

Primaere Methode — Discord OAuth2:
- Login ueber "Mit Discord anmelden"-Button (OAuth2 Authorization Code Flow)
- Berechtigung ueber Discord-Rollen ODER User-ID: User mit bestimmten Rollen (z.B. @Admin) oder explizit eingetragene User-IDs erhalten Zugang
- Dashboard fragt beim OAuth2-Callback die Guild-Mitgliedschaft + Rollen ab (Scope: `identify guilds.members.read`)
- Berechtigte Rollen konfigurierbar in `config.json` (Liste von Rollen-IDs)
- Zusaetzlich: Whitelist mit Discord User-IDs in `config.json` (fuer Zugang unabhaengig von Rollen)
- Zugang wird gewaehrt wenn User EINE der Bedingungen erfuellt (berechtigte Rolle ODER gelistete User-ID)
- Nach erfolgreichem Login: Session-Cookie (JWT) mit Discord User-ID, Username, Avatar
- Sidebar zeigt Discord-Avatar + Username des eingeloggten Users
- Automatischer Logout wenn User die berechtigte Rolle auf Discord verliert (Check bei jedem Request oder alle X Minuten)

Fallback-Methode — Username + Passwort:
- Klassischer Login mit Username + Passwort (bcrypt-gehashed)
- Fuer Notfaelle wenn Discord OAuth2 nicht verfuegbar ist (Discord-Ausfall, API-Probleme)
- Ein Notfall-Admin-Account in `.env` konfigurierbar (`WEB_ADMIN_USER`, `WEB_ADMIN_PASS_HASH`)
- Rate-Limiting fuer Login-Versuche (max. 5 Versuche pro 15 Min)

Login-Seite:
- Grosser "Mit Discord anmelden"-Button (primaer, hervorgehoben)
- Darunter Trennlinie mit "oder"
- Kompaktes Username/Passwort-Formular (sekundaer, dezent)
- Dark Theme passend zum Dashboard
- Fehlermeldungen: "Keine Berechtigung (fehlende Rolle)", "Discord nicht erreichbar", "Falsches Passwort"

Technisch:
- OAuth2 Redirect URI: `https://dashboard.example.com/auth/discord/callback`
- Discord Application: Client ID + Client Secret in `.env`
- Session-basiert (JWT Cookie, httpOnly, secure)
- CSRF-Protection fuer Login-Formulare

**Architektur:**
```
web/
  app.py              — FastAPI Application + WebSocket Handler
  auth.py             — Login, Session, Passwort-Hashing, Discord OAuth2
  routes/
    dashboard.py      — Uebersicht (Startseite)
    server_detail.py  — Server-Detailseite (Spieler, Backups, RCON, Mods, Analyse, etc.)
    errors.py         — Fehler-Uebersicht
    admin_bot.py      — Admin Bot Setup (Temp Voice, TS, Moderation)
    config.py         — Config-Panel (Feature #14)
    system.py         — System/Webmin-Einbettung
  templates/
    base.html         — Layout mit Sidebar-Navigation (Dark Theme)
    dashboard.html    — Startseite mit Server-Kacheln + Events
    server_detail.html— Server-Detail (Spieler, Backups, RCON, Config, Mods, Analyse)
    errors.html       — Fehler-Uebersicht (ERROR/WARNING)
    login.html        — Login-Seite (Discord OAuth2 + Passwort-Fallback)
    admin_bot.html    — Admin Bot Setup (10 Tabs)
    config.html       — Config-Panel (Formular)
    system.html       — Webmin iframe
  static/
    style.css         — Eigenes Stylesheet (Dark Theme)
    htmx.min.js       — HTMX Library
```

**Sidebar-Navigation (6 Seiten):**
1. 📊 Uebersicht (Startseite + Quick-Actions)
2. ⚠️ Fehler (ERROR/WARNING Log)
3. 🛡️ Admin Bot (Setup: Temp Voice, TS, Moderation)
4. ⚙️ Config (Bot-Konfiguration)
5. 🖥️ System (Webmin iframe)
6. ↩️ Logout

Server-Detail ist keine eigene Sidebar-Seite, sondern per Klick auf eine Server-Kachel erreichbar (Unterseite).

**WebSocket-Events (Server → Client):**
- `server_status`: Status-Aenderung eines Servers
- `player_join` / `player_leave`: Spieler-Events
- `log_entry`: Neue Log-Zeile
- `backup_progress`: Backup-Fortschritt
- `system_stats`: CPU/RAM/Disk-Updates (alle 5s)
- `bot_ping`: Discord-Latenz pro Bot (alle 10s) — fuer Bot-Status-Leiste auf der Uebersicht

**Integration mit bestehenden Bots:**
- Dashboard liest direkt die bestehenden JSON-Dateien (`data/*.json`)
- Server-Steuerung: Ruft die gleichen Module auf wie die Discord-Bots
- Analyse-Daten: Monitor Bot schreibt `data/monitor/stats_history.json` (via `modules/monitoring/stats_collector.py`), Dashboard liest und visualisiert
- Shared `utils/` und `modules/` werden importiert
- Laeuft als eigener systemd Service (`web-dashboard.service`)

**Deployment:**
- Eigener Port (z.B. 8080), hinter Nginx Reverse-Proxy
- SSL ueber Let's Encrypt (bestehende Nginx-Config erweitern)
- Subdomain z.B. `dashboard.example.com`
- Webmin Reverse-Proxy: `/webmin/` → `localhost:9090` (fuer iframe-Einbettung)

**Aufwand:** ~25-35 Stunden (inkl. Feature #14 Config-Panel)
**Dateien:** Neues `web/` Verzeichnis (siehe Architektur oben)
**Abhaengigkeiten:** `fastapi`, `uvicorn`, `python-jose` (JWT), `bcrypt`, `websockets`, `httpx` (fuer Discord OAuth2 API Calls) (pip)
**ENV-Variablen:** `WEB_ENABLED`, `WEB_PORT`, `WEB_SECRET_KEY`, `WEB_ADMIN_USER`, `WEB_ADMIN_PASS_HASH`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `DISCORD_GUILD_ID`
**Config-Variablen:** `web_allowed_role_ids` (Liste), `web_allowed_user_ids` (Liste)

### 14. Discord-Bot-Konfiguration via Web-UI

In Feature #13 (Web-Dashboard) integriertes Config-Panel. Ermoeglicht das Aendern von `config.json`-Settings direkt im Browser ohne SSH-Zugang.

**Config-Bereiche (Formular-Gruppen):**

1. **Feature-Toggles**
   - Monitoring ein/aus, Backup ein/aus, OneDrive-Sync, Chat-Bridge, WordFilter, AntiSpam
   - Dargestellt als Toggle-Switches

2. **Intervalle und Timer**
   - Health-Check-Intervall, Backup-Rotation, Player-Tracking-Intervall
   - Web-Status-Update-Intervall, Modpack-Check-Intervall
   - Dargestellt als Zahleneingabe mit Einheit (Sekunden/Minuten)

3. **Schwellwerte und Limits**
   - CPU/RAM/Disk-Warnschwellen, Max-Backups pro Server
   - AntiSpam-Limits (Nachrichten/Zeitfenster), Restart-Cooldown
   - Dargestellt als Slider oder Zahleneingabe

4. **Benachrichtigungen (Routing-Matrix)**
   Zentrales Management: Welche Benachrichtigung geht wohin. Pro Ereignis-Typ konfigurierbar:

   Ereignis-Typen:
   - **Server-Status:** Start, Stop, Crash, Restart (pro Server: SAT, BMC, Vanilla)
   - **Updates:** Update verfuegbar, Auto-Update ausgefuehrt, Update fehlgeschlagen
   - **Backups:** Backup erstellt, Backup fehlgeschlagen, OneDrive-Sync Status
   - **Performance:** CPU/RAM/Disk-Warnung, TPS-Drop (MC), Health-Check fehlgeschlagen
   - **Spieler:** Join, Leave, Kick, Ban (pro Server)
   - **Moderation:** Warn, Mute, Ban, AntiSpam-Trigger, WordFilter-Trigger
   - **System:** Bot-Neustart, Config-Aenderung, Selftest-Ergebnis, Disk-Space-Warnung

   Ziel-Kanaele (pro Ereignis konfigurierbar):
   - **Admin-Channel:** Nur fuer Admins sichtbar (technische Infos, Fehler)
   - **Spieler-Channel:** Fuer alle Spieler sichtbar (Updates, Server-Status)
   - **Log-Channel:** Ausfuehrliches Logging (alle Events)
   - **Mod-Channel:** Moderations-Aktionen (Warns, Bans, Spam)
   - **Keiner:** Benachrichtigung deaktiviert
   - Zusaetzlich: E-Mail ein/aus pro Ereignis-Typ (falls E-Mail-Notifier aktiv)

   Darstellung im Dashboard:
   - Tabelle/Matrix: Zeilen = Ereignis-Typ, Spalten = Ziel (Admin/Spieler/Log/Mod/Aus/E-Mail)
   - Pro Zeile ein Dropdown oder Radio-Buttons fuer den Ziel-Channel
   - E-Mail als zusaetzliche Checkbox pro Zeile
   - Gruppiert nach Kategorie (Server, Updates, Backups, Performance, Spieler, Moderation, System)
   - Standard-Voreinstellungen beim ersten Setup (z.B. Fehler → Admin, Spieler-Events → Log, Updates → Spieler)
   - **Test-Button** pro Zeile: Sendet eine Test-Nachricht in den konfigurierten Ziel-Channel (z.B. "🔔 Test: Server-Status Benachrichtigung — korrekt konfiguriert"), damit man die Zuordnung verifizieren kann bevor man speichert

   Technisch:
   - Persistenz: `config.json` unter `notification_routing` (Dict: Event-Typ → Channel-ID + E-Mail-Toggle)
   - `discord_notifier.py`: Routing-Logik — liest Ziel-Channel aus Config statt hardcodierter Channel-IDs
   - Fallback: Wenn kein Channel konfiguriert → Admin-Channel als Default

5. **Dashboard-Login-Verwaltung**
   - Berechtigte Discord-Rollen verwalten (Rollen-IDs hinzufuegen/entfernen)
   - Berechtigte Discord User-IDs verwalten (Whitelist hinzufuegen/entfernen)
   - Aktive Sessions anzeigen (wer ist eingeloggt, wann, via Discord/Passwort)
   - Notfall-Admin-Passwort aendern
   - OAuth2 Status anzeigen (verbunden/getrennt, Client ID Info)

6. **Bot-Profile (Anzeigenamen + Avatare)**
   - Pro Bot (GameServer, Monitor, Admin): Anzeigename aendern (`bot.user.edit(username=...)`)
   - Pro Bot: Avatar/Profilbild hochladen und setzen (`bot.user.edit(avatar=...)`)
   - Vorschau: Aktuelles Profilbild + Name wird angezeigt
   - Hinweis: Discord Rate-Limit fuer Profilaenderungen (2x pro Stunde) wird angezeigt
   - Bot-Status setzen: Activity-Text pro Bot (z.B. "Spielt Satisfactory" / "Ueberwacht 3 Server")

**Funktionen:**
- Validierung vor dem Speichern (Typ-Check, Min/Max-Werte)
- Hot-Reload: Bots laden Config nach Aenderung automatisch neu (via File-Watcher oder Signal)
- Aenderungs-Historie: Wer hat wann welchen Wert geaendert (`data/config_changelog.json`)
- Rollback: Letzte Aenderung rueckgaengig machen

**Aufwand:** In Feature #13 enthalten (~5-8h des Gesamtaufwands)
**Dateien:** `web/routes/config.py`, `web/templates/config.html`, Erweiterung `utils/config.py` (Hot-Reload)
**Abhaengigkeiten:** Feature #13 (Web-Dashboard)

### 17. Discord Temp Voice Channels

Join-to-Create-System fuer temporaere Voice Channels mit Embed-basierter Kanalverwaltung. Ein einzelner Hub-Voice-Channel dient als Trigger — sobald ein User diesem Channel beitritt, wird automatisch ein neuer temporaerer Voice Channel in einer dedizierten Kategorie erstellt und der User dorthin verschoben. Sobald der letzte User den Channel verlaesst, wird er sofort geloescht. Die gesamte Kanalverwaltung erfolgt ueber ein interaktives Embed im Text-in-Voice-Chat — keine Slash-Commands fuer Owner.

**Ablauf:**
1. Admin richtet per `/tempvoice setup <hub_channel> <kategorie>` den Hub-Channel und die Ziel-Kategorie ein
2. User tritt dem Hub-Channel bei → Bot erstellt "{Username}'s Channel" in der Temp-Kategorie
3. User wird automatisch in den neuen Channel verschoben
4. Bot postet das Verwaltungs-Embed im Text-in-Voice-Chat des neuen Channels
5. Berechtigungen werden vom Hub-Channel geerbt + Owner erhaelt Sonderrechte
6. Letzter User verlaesst den Channel → Channel wird sofort geloescht

**Verwaltungs-Embed (Text-in-Voice):**

Das Embed wird automatisch beim Erstellen des Temp-Channels im integrierten Text-Chat gepostet. Alle Buttons und Aktionen sind nur fuer den Channel-Owner sichtbar/nutzbar. Das Embed zeigt den aktuellen Kanalstatus und bietet folgende Steuerungselemente:

```
┌─────────────────────────────────────────────┐
│  🔊 {Username}'s Channel                    │
│                                              │
│  👑 Owner: @Username                        │
│  👥 Nutzer: 3/8                             │
│  📝 Status: Wartet auf Mitspieler           │
│  🔓 Zugang: Offen                           │
│  ⏰ AFK-Timer: Aus                          │
│                                              │
│  [🔒 Sperren]  [📝 Status]  [✏️ Name]      │
│  [👥 Limit]    [📄 Beschreibung]            │
│  [➕ Einladen] [❌ Ausschliessen]            │
│  [⏰ AFK-Timer] [👑 Rechte uebertragen]     │
└─────────────────────────────────────────────┘
```

**Embed-Buttons und Aktionen:**

| Button | Aktion | Interaktion |
|--------|--------|-------------|
| 🔒 Sperren / 🔓 Entsperren | Kanal fuer neue User sperren/entsperren | Toggle-Button, wechselt Text und Icon |
| 📝 Status | Kanalstatus aendern (Text unter dem Channelnamen) | Modal mit Texteingabe (Discord Voice Channel Status, zeichenbegrenzt) |
| ✏️ Name | Kanalname aendern | Modal mit Texteingabe |
| 👥 Limit | Nutzer-Limit festlegen | Modal mit Zahleneingabe (0 = unbegrenzt) |
| 📄 Beschreibung | Kanalbeschreibung aendern | Modal mit Texteingabe (mehrzeilig) |
| ➕ Einladen | User in den Kanal einladen (auch bei gesperrtem Kanal) | User-Select-Menu (Dropdown mit Server-Mitgliedern) |
| ❌ Ausschliessen | User aus dem Kanal entfernen + Zugang sperren | User-Select-Menu (Dropdown mit Kanal-Mitgliedern) |
| ⏰ AFK-Timer | Idle-Timer aktivieren/deaktivieren | Modal mit Zeiteingabe in Minuten (0 = aus) |
| 👑 Rechte uebertragen | Ownership an anderen User uebertragen | User-Select-Menu (Dropdown mit Kanal-Mitgliedern) |

**Ownership-Transfer:**
- Manuell: Owner klickt "Rechte uebertragen", waehlt User per Dropdown, bestaetigt → neuer Owner
- Automatisch: Wenn Owner den Channel verlaesst, wird der User mit der laengsten Anwesenheit neuer Owner
- Embed wird nach Transfer aktualisiert (neuer Owner-Name, Buttons reagieren auf neuen Owner)
- Falls der letzte User geht → Channel wird geloescht (kein Transfer noetig)

**AFK-Handling:**
- Owner kann per Embed-Button einen AFK-Timer setzen (z.B. 10 Minuten)
- User die laenger als X Minuten idle sind (kein Sprechen, kein Mute-Toggle) werden automatisch aus dem Channel entfernt
- AFK-Timer wird im Embed angezeigt (z.B. "⏰ AFK-Timer: 10 Min")
- Betroffene User erhalten eine kurze DM-Benachrichtigung (optional, konfigurierbar)
- Owner ist vom AFK-Timer ausgenommen

**Admin-Commands (nur fuer Server-Admins, per Slash-Command):**
- `/tempvoice setup <hub_channel> <kategorie>` — Hub-Channel und Kategorie festlegen
- `/tempvoice settings` — Aktuelle Konfiguration anzeigen
- `/tempvoice reset` — Konfiguration zuruecksetzen (loescht alle aktiven Temp-Channels)

**Technische Details:**
- Standard-Benennung: "{Username}'s Channel"
- Berechtigungen: Geerbt vom Hub-Channel + Permission-Overrides fuer Owner
- Temp-Channels in eigener Discord-Kategorie (z.B. "Temp Channels")
- Embed-Interaktion: `discord.ui.View` mit `Button` und `UserSelect` Komponenten
- Modals: `discord.ui.Modal` fuer Texteingaben (Name, Status, Beschreibung, Limit, AFK)
- Kanalstatus: `channel.edit(status="...")` (Discord Voice Channel Status Feature)
- Persistenz: `data/temp_voice.json` (Hub-Config, aktive Channel-IDs, Owner-Mapping, AFK-Timer)
- Listener: `on_voice_state_update` Event im Admin Bot
- Bei Bot-Neustart: Verwaiste Temp-Channels (aus JSON) aufraeumen, Embeds neu posten
- AFK-Check: Background-Task der alle 60s idle-Zeiten prueft

**Aufwand:** ~6-8 Stunden
**Dateien:** Neuer Cog `cogs/temp_voice_cog.py`, Neues Modul `modules/temp_voice.py`, Views `modules/temp_voice_views.py`
**Abhaengigkeiten:** Keine (discord.py Voice-State-Events, discord.ui Components)

### 16. TeamSpeak-Integration (3 Phasen)

Schrittweise Integration eines TeamSpeak-Servers in das Bot-System ueber die TeamSpeak ServerQuery-Schnittstelle. Alle TS-Befehle werden per Discord-Slash-Commands gesteuert. Bot-Architektur (eigener Bot vs. Cog im GameServer Bot) wird bei Implementierung entschieden.

**Technologie:** TeamSpeak ServerQuery (TCP) via `ts3` oder `ts3API` Python-Library (async-kompatibel)

#### Phase 1: Status + User-Verwaltung (~6-8h)

Grundlegende TS-Server-Anbindung mit Status-Abfrage und User-Management per Discord.

**Commands:**
- `/ts status` — Server-Status (Online/Offline, Uptime, Version, Spieleranzahl)
- `/ts players` — Aktuelle User-Liste (Name, Channel, Idle-Zeit, Plattform)
- `/ts info` — Server-Details (Slots, Bandbreite, Ping, Paketverlust)
- `/ts kick <user> [grund]` — User vom TS kicken
- `/ts ban <user> <dauer> [grund]` — User temporaer/permanent bannen
- `/ts unban <user>` — Ban aufheben
- `/ts banlist` — Aktive Bans anzeigen
- `/ts poke <user> <nachricht>` — Nachricht an einzelnen User senden
- `/ts move <user> <channel>` — User in anderen Channel verschieben

**Dateien:** `modules/teamspeak/ts_client.py` (ServerQuery-Wrapper), `modules/teamspeak/ts_manager.py` (Business-Logik), `cogs/teamspeak_cog.py`
**Abhaengigkeiten:** `ts3` Library (pip), TeamSpeak-Server mit ServerQuery-Zugang
**ENV-Variablen:** `TS_ENABLED`, `TS_HOST`, `TS_QUERY_PORT`, `TS_QUERY_USER`, `TS_QUERY_PASS`, `TS_SERVER_ID`, `TS_DISCORD_CHANNEL`

#### Phase 2: Chat-Bridge (~4-6h)

Bidirektionale Chat-Bridge zwischen TeamSpeak Server-Chat und einem Discord Text-Channel.

**Features:**
- TS → Discord: Nachrichten aus dem TS-Server-Chat werden in einen Discord-Channel weitergeleitet
- Discord → TS: Nachrichten aus dem Discord-Channel werden als Server-Nachricht im TS gepostet
- Formatierung: TS-BBCode ↔ Discord-Markdown Konvertierung
- Konfigurierbar: Ein-/Ausschaltbar per `/ts config chat_bridge <on/off>`
- Word-Filter Integration: Bestehender WordFilter greift auch auf Bridge-Nachrichten

**Dateien:** `modules/teamspeak/chat_bridge.py`, Erweiterung `cogs/teamspeak_cog.py`
**Abhaengigkeiten:** Phase 1 (TS-Client), funktionierender ServerQuery Event-Listener
**ENV-Variablen:** `TS_CHAT_BRIDGE_ENABLED`, `TS_CHAT_BRIDGE_CHANNEL`

#### Phase 3: Channel-Management + Gameserver-Automatisierung (~5-8h)

Erweiterte Channel-Verwaltung und automatische Verknuepfung mit Gameserver-Events.

**Channel-Commands:**
- `/ts channel list` — Channel-Uebersicht mit Verschachtelung und User-Anzahl
- `/ts channel create <name> [parent]` — Channel erstellen
- `/ts channel delete <name>` — Channel loeschen
- `/ts channel edit <name> <setting> <wert>` — Channel bearbeiten (Passwort, Limit, Codec)

**Gameserver-Automatisierung:**
- Auto-Channel: Wenn ein Gameserver startet (SAT/MC), wird ein passender TS-Channel erstellt
- Auto-Delete: Wenn der Gameserver stoppt, wird der Channel nach X Minuten geloescht (falls leer)
- Poke-Benachrichtigung: Online-TS-User per Poke benachrichtigen wenn ein Gameserver startet
- Konfigurierbar per `config.json`: Welche Server welche Channels triggern

**Dateien:** `modules/teamspeak/channel_manager.py`, `modules/teamspeak/auto_channels.py`, Erweiterung `cogs/teamspeak_cog.py`
**Abhaengigkeiten:** Phase 1 (TS-Client), Monitor Bot fuer Gameserver-Events

**Gesamtaufwand:** ~15-22 Stunden (alle 3 Phasen)
**Hauptverzeichnis:** `modules/teamspeak/` mit Query-Client, Manager, Chat-Bridge, Channel-Manager
**Voraussetzung:** TeamSpeak-Server mit aktiviertem ServerQuery-Zugang

### 18. Admin Bot — Dritter Bot fuer Server-Verwaltung

Eigenstaendiger dritter Discord-Bot, der sich ausschliesslich um Server-Verwaltung kuemmert: Discord-Moderation und TeamSpeak-Steuerung. Damit entsteht eine klare Drei-Bot-Architektur:

| Bot | Zustaendigkeit |
|-----|---------------|
| **GameServer Bot** | Gameserver-Steuerung (SAT + MC), In-Game-Moderation (RCON WordFilter/AntiSpam) |
| **Monitor Bot** | Hintergrund-Tasks (Health Checks, Player-Tracking, Web-Status, Backups) |
| **Admin Bot (neu)** | Discord-Moderation, TeamSpeak-Steuerung, Temp Voice, Community-Features |

**Features im Admin Bot (vollstaendiger Maki-Ersatz):**
- Feature #17: Discord Temp Voice Channels (Join-to-Create mit Embed-Verwaltung)
- Feature #16: TeamSpeak-Integration (3 Phasen: Status, Chat-Bridge, Channel-Management)
- Discord WordFilter: Migration aus GameServer Bot — filtert Discord-Nachrichten (nicht RCON/In-Game)
- Discord AntiSpam: Migration aus GameServer Bot — erkennt Spam in Discord-Channels
- Discord-Moderation: Warn-System (Stufen + Punkte), Mute/Timeout, Ban mit Logging
- Reaction Roles: Embed + Emoji-Reaktion → Rollen-Zuweisung/Entfernung
- Leveling/XP-System: XP fuer Text + Voice, Level-Ups, Rollen-Rewards, Leaderboard, XP-Multiplikatoren
- Ticket-System: Support-Tickets per Button (Details bei Implementierung)
- Audit-Logging: Moderation-Log, Join/Leave-Log, Member-Log, Role-Log, Server-Log
- Giveaway-System: Giveaways per Embed + Reaktion/Button, automatische Gewinner-Ziehung, Reroll

**Migration WordFilter + AntiSpam:**
- Discord-seitige Filterung (Nachrichten in Text-Channels) → wandert in den Admin Bot
- Gameserver-seitige Filterung (RCON-Inputs, In-Game-Chat) → bleibt im GameServer Bot
- Bestehende Module werden aufgeteilt: Discord-Filter in `modules/moderation/`, RCON-Filter bleibt in `modules/`
- Konfiguration bleibt in der gemeinsamen `config.json` (gleiche Wortlisten, getrennte Toggles)

**Daten-Architektur (getrennte Ordner):**
```
data/
  gameserver/     — GameServer Bot Daten (Blacklist, Savegames, etc.)
  monitor/        — Monitor Bot Daten (Player-Tracking, Health-History, etc.)
  admin/          — Admin Bot Daten (temp_voice.json, moderation.json, warns.json, etc.)
```
- Jeder Bot hat seinen eigenen Unterordner unter `data/`
- Kein Konfliktpotential bei gleichzeitigen Schreibzugriffen
- Inter-Bot-Kommunikation: Bots koennen JSON-Dateien anderer Bots lesen (z.B. Admin Bot liest `data/monitor/server_status.json`)
- Migration: Einmaliger Umbau der Pfade in allen Modulen (Teil des Grundgeruest-Aufwands)

**Reaction Roles — Details:**
- Admin erstellt per `/reactionrole create <channel> <titel> <beschreibung>` ein Embed
- Per `/reactionrole add <message_id> <emoji> <rolle>` werden Emoji-Rollen-Paare hinzugefuegt
- User reagiert mit Emoji → Rolle wird zugewiesen; Reaktion entfernen → Rolle entfernt
- Mehrere Embeds moeglich (z.B. Farb-Rollen, Spiel-Rollen, Benachrichtigungs-Rollen)
- Persistenz: `data/admin/reaction_roles.json` (Message-ID → Emoji → Rolle Mapping)
- Bei Bot-Neustart: Bestehende Reaction-Role-Messages wieder registrieren

**Leveling/XP-System — Details:**
- XP-Vergabe: Pro Nachricht (konfigurierbar, z.B. 15-25 XP, Cooldown 60s) + pro Voice-Minute (z.B. 5 XP/Min)
- Level-Formel: XP fuer Level N = 5 * N² + 50 * N + 100 (oder konfigurierbar)
- Level-Up: Benachrichtigung im Channel oder per DM (konfigurierbar)
- Rollen-Rewards: Bei bestimmten Leveln automatisch Rollen zuweisen (z.B. Level 5 → @Aktiv, Level 15 → @Stammgast)
- XP-Multiplikatoren: Pro Channel konfigurierbar (z.B. #off-topic 0.5x, #projekte 2x)
- Leaderboard: `/rank` zeigt eigenen Rang, `/leaderboard` zeigt Top 10/20 als Embed
- Voice-XP: Nur wenn nicht gemutet und mindestens 2 User im Channel
- Anti-Exploit: Spam-Nachrichten geben kein XP (AntiSpam-Integration)
- Persistenz: `data/admin/leveling.json` (User-ID → XP, Level, Voice-Zeit)
- Dashboard-Konfiguration: XP-Raten, Cooldowns, Multiplikatoren, Rollen-Rewards, Level-Up-Channel

**Ticket-System — Grundkonzept:**
- Embed mit Button in einem Channel (z.B. #support)
- Klick auf Button → privater Thread oder Channel wird erstellt
- Nur der Ersteller und Admins/Support-Rollen koennen den Channel sehen
- Ticket schliessen: Admin-Button oder Command, Transcript wird gespeichert
- Weitere Details (Kategorien, Formulare) bei Implementierung klaeren
- Persistenz: `data/admin/tickets.json`

**Audit-Logging — Details:**
- Separate Log-Channels pro Kategorie (konfigurierbar im Dashboard):
  - **Moderation-Log:** Warns, Mutes, Kicks, Bans, Unmutes, Unbans
  - **Join/Leave-Log:** Member Joins, Leaves, Account-Alter bei Join
  - **Member-Log:** Nickname-Aenderungen, Avatar-Aenderungen
  - **Role-Log:** Rollen hinzugefuegt/entfernt (pro User)
  - **Server-Log:** Channel erstellt/geloescht, Rollen erstellt/geloescht, Server-Settings geaendert
- Embeds mit Timestamp, betroffenem User, ausfuehrendem Admin (bei Mod-Actions)
- Persistenz: Nur in Discord-Channels (kein JSON-Logging noetig)

**Giveaway-System — Details:**
- Admin erstellt per `/giveaway create <preis> <dauer> [gewinner] [channel]` ein Giveaway-Embed
- User nehmen per Emoji-Reaktion (🎉) oder Button-Klick teil
- Nach Ablauf der Dauer werden Gewinner automatisch gezogen und im Channel getaggt
- Gewinner erhalten eine DM-Benachrichtigung mit Preis-Details
- `/giveaway reroll <message_id>` — Neue Gewinner ziehen (falls Gewinner nicht erreichbar)
- `/giveaway end <message_id>` — Giveaway vorzeitig beenden und Gewinner ziehen
- `/giveaway cancel <message_id>` — Giveaway abbrechen (keine Ziehung)
- `/giveaway list` — Alle laufenden Giveaways anzeigen
- Optionale Teilnahmebedingungen: Mindest-Level (Leveling-Integration), bestimmte Rolle, Mindest-Mitgliedsdauer
- Persistenz: `data/admin/giveaways.json` (Message-ID → Preis, Dauer, Teilnehmer, Gewinner, Bedingungen)
- Background-Task prueft regelmaessig ob Giveaways abgelaufen sind
- Dashboard-Konfiguration: Standard-Channel, Gewinner-Benachrichtigungen, Teilnahme-Methode

**Technische Eckpunkte:**
- Eigener Discord Bot-Token (separater Bot-Account)
- Eigener systemd Service (`admin-bot.service`)
- Einstiegspunkt: `bots/admin_bot.py`
- Cogs: `cogs/temp_voice_cog.py`, `cogs/teamspeak_cog.py`, `cogs/moderation_cog.py`, `cogs/reaction_roles_cog.py`, `cogs/leveling_cog.py`, `cogs/tickets_cog.py`, `cogs/audit_cog.py`, `cogs/giveaway_cog.py`
- Module: `modules/temp_voice.py`, `modules/teamspeak/`, `modules/moderation/`, `modules/leveling.py`, `modules/tickets.py`, `modules/audit_logger.py`, `modules/giveaways.py`
- Shared Utils: Greift auf bestehende `utils/` zu (Config, Logger, Permissions)
- Deployment analog zu den anderen Bots (SCP + systemctl)
- Discord Intents: Privileged Intents noetig (Members, Message Content, Presences fuer Voice-XP)

**Aufwand:** ~20-30 Stunden (Grundgeruest + alle Module inkl. Maki-Ersatz)
**Dateien:** `bots/admin_bot.py`, `systemd/admin-bot.service`, neue Cogs + Module, Umbau `data/`-Struktur
**Abhaengigkeiten:** Neuer Discord Bot-Token
**ENV-Variablen:** `ADMIN_BOT_TOKEN`, `ADMIN_BOT_PREFIX`, `ADMIN_DATA_DIR`

### 19. Discord + TeamSpeak Server-Backup (Struktur-Snapshot)

Vollstaendiges Backup der Discord- und TeamSpeak-Server-Struktur als JSON-Snapshot. Ermoeglicht es, den kompletten Aufbau (Channels, Rollen, Berechtigungen, Einstellungen) jederzeit wiederherzustellen — z.B. fuer einen neuen Server oder nach versehentlichen Aenderungen.

**Was wird gesichert?**

Discord-Server:
- **Channels:** Alle Channels und Kategorien mit Name, Typ, Position, Sortierung, Topic, Slowmode, NSFW-Flag
- **Channel-Berechtigungen:** Permission-Overwrites pro Channel (Rollen + User)
- **Rollen:** Alle Rollen mit Name, Farbe, Position, Berechtigungen (Permissions Bitfield), hoist, mentionable
- **Server-Einstellungen:** Servername, Icon, Banner, Region, Verification Level, Default Notifications, AFK-Channel/-Timeout, System-Channel
- **Emoji + Sticker:** Name, ID, animiert ja/nein (Bilddaten separat)
- **Willkommensnachricht:** System-Channel-Einstellungen (Join-Messages, Boost-Messages)

TeamSpeak-Server (wenn TS-Integration aktiv):
- **Channels:** Alle Channels mit Name, Parent, Sortierung, Passwort-geschuetzt ja/nein, Talk Power, Codec, Max-Clients
- **Channel-Gruppen:** Alle Channel-Gruppen mit Berechtigungen
- **Server-Gruppen:** Alle Server-Gruppen mit Berechtigungen und Zuweisungen
- **Server-Einstellungen:** Servername, Willkommensnachricht, Max-Clients, Anti-Flood-Settings
- **Bans:** Aktive Ban-Liste (IP, UID, Name, Grund, Dauer)

**Was wird NICHT gesichert:**
- Nachrichten / Chat-Historie (zu gross, nicht sinnvoll)
- User-Avatare / Profilbilder
- Voice-States / Online-Status
- Bot-Token oder Secrets

**Commands:**
- `/server backup create [beschreibung]` — Snapshot erstellen (Discord + TS wenn aktiv)
- `/server backup list` — Alle Snapshots anzeigen (Datum, Groesse, Beschreibung)
- `/server backup info <id>` — Details eines Snapshots (Channels, Rollen, Aenderungen seit Snapshot)
- `/server backup restore <id> [modus]` — Snapshot wiederherstellen (Modi: siehe unten)
- `/server backup delete <id>` — Snapshot loeschen
- `/server backup compare <id>` — Aktuellen Server mit Snapshot vergleichen (Diff-Ansicht)
- `/server backup auto` — Auto-Backup Einstellungen (Intervall, Rotation)

**Wiederherstellungs-Modi:**
- `vollstaendig` — Erstellt alle fehlenden Channels/Rollen, loescht ueberfluessige, setzt alle Berechtigungen zurueck
- `ergaenzen` — Erstellt nur fehlende Channels/Rollen, aendert nichts Bestehendes
- `nur_rollen` — Nur Rollen + Berechtigungen wiederherstellen
- `nur_channels` — Nur Channel-Struktur wiederherstellen
- Sicherheitsabfrage vor Restore: Embed mit Diff-Zusammenfassung + Bestaetigungs-Button

**Auto-Backup:**
- Konfigurierbares Intervall (z.B. taeglich, woechentlich)
- Rotation: Max. X Snapshots behalten, aelteste loeschen
- Optional: Backup bei erkannten Strukturaenderungen (Channel erstellt/geloescht, Rolle erstellt/geloescht)

**Technische Details:**
- Snapshot-Format: JSON-Datei mit Timestamp, Server-ID, Version, vollstaendiger Struktur
- Speicherort: `data/admin/server_backups/` (ein JSON pro Snapshot)
- Discord-API: `guild.channels`, `guild.roles`, `guild.emojis`, `guild.stickers` + Permission-Overwrites
- TeamSpeak: ServerQuery Befehle `channellist`, `servergrouplist`, `channelgrouplist`, `serverinfo`
- Restore: Reihenfolge beachten — erst Rollen (wegen Referenzen), dann Kategorien, dann Channels, dann Overwrites
- Rate-Limiting: Discord API hat Limits fuer Channel/Rollen-Erstellung (~30/Stunde) → Restore mit Pausen
- Diff-Berechnung: Snapshot vs. aktuelle Struktur → zeigt hinzugefuegte, entfernte, geaenderte Elemente

**Aufwand:** ~8-12 Stunden
**Dateien:** `modules/server_backup.py`, `cogs/server_backup_cog.py`
**Abhaengigkeiten:** Feature #18 (Admin Bot), Feature #16 Phase 1 (TS-Client, optional)
**Persistenz:** `data/admin/server_backups/*.json`

### 20. SAT Auto-Update Verbesserung (Sofort bei leerem Server + Spieler-Benachrichtigung)

Erweiterung des bestehenden Auto-Update-Systems fuer Satisfactory. Aktuell wird nur zu einer festen Uhrzeit (`auto_update_hour`, Standard 5:00) geprüft und installiert. Neues Verhalten: Sobald ein Update erkannt wird UND keine Spieler mehr online sind, wird sofort aktualisiert — unabhaengig von der Uhrzeit.

**Aktuelles Verhalten (v3.1.0):**
- Update-Check periodisch (alle X Stunden)
- Wenn Update verfuegbar → Benachrichtigung an Admin-Channel
- Auto-Install nur zur festen Uhrzeit (`auto_update_hour`) und nur wenn `require_empty = true` und Server leer
- Benachrichtigung nur im Admin-Channel

**Neues Verhalten:**
- Update-Check bleibt periodisch (alle X Stunden)
- Wenn Update verfuegbar → sofort `_pending_update = True` setzen
- Bei jedem Player-Leave-Event: Pruefen ob `_pending_update` und Spieleranzahl == 0
- Wenn ja → Auto-Update sofort ausfuehren (Backup → Stop → SteamCMD → Start)
- Die feste Uhrzeit (`auto_update_hour`) entfaellt als Bedingung — Update erfolgt beim naechsten "Server leer"-Moment
- Falls Server bereits offline ist und Update pending → beim naechsten Scheduler-Tick direkt updaten

**Auto-Rollback bei fehlgeschlagenem Update:**
- Nach dem Update prueft der Bot ob der Server innerhalb von 3 Minuten wieder online ist (Health-Check)
- Wenn Server nach 3 Minuten nicht erreichbar → automatischer Rollback:
  1. Server stoppen (falls haengend)
  2. Pre-Update-Backup wiederherstellen (wurde vor dem Update automatisch erstellt)
  3. Server neu starten
  4. Admin-Channel: Warnung mit Fehlerdetails ("Auto-Update auf Version X fehlgeschlagen, Rollback auf vorherige Version ausgefuehrt")
- Wenn Server nach Rollback immer noch nicht startet → nur Admin-Benachrichtigung (manueller Eingriff noetig)
- Rollback-Versuch wird in der Update-Historie vermerkt

**Spieler-Benachrichtigung (Discord):**
- Nach erfolgreichem Update: Nachricht im konfigurierten Ziel-Channel (Standard: Spieler-Channel)
- Embed mit: "Satisfactory Server wurde auf Version X aktualisiert und ist wieder online"
- Alte und neue Build-ID anzeigen
- Optional: Changelog-Link (wenn SteamDB/Patch Notes verfuegbar)
- Bei fehlgeschlagenem Update: Nur Admin-Channel (Spieler nicht mit Fehlern belaestigen)
- Ziel-Channel fuer Update-Benachrichtigungen konfigurierbar ueber Benachrichtigungs-Routing im Dashboard (Feature #14)

**Minecraft — Keine Aenderung:**
- BMC + Vanilla: Weiterhin nur Benachrichtigung bei verfuegbarem Update
- Kein Auto-Update fuer MC (Modpack-Kompatibilitaet, manuelle Pruefung noetig)

**Technische Aenderungen:**
- `scheduler_cog.py`: `_check_auto_update_install()` umbauen — Uhrzeitbedingung entfernen, stattdessen auf Player-Leave reagieren
- Neuer Listener: `on_player_leave` (oder bestehenden Player-Tracker nutzen) → triggert Update-Check
- `discord_notifier.py`: Neue Methode `notify_player_update(old_version, new_version)` → postet im Spieler-Channel
- Config: `auto_update_mode` statt `auto_update_hour` — Wert `on_empty` (neu, Standard) oder `scheduled` (altes Verhalten)
- Config: `sat_player_channel_id` fuer Spieler-Benachrichtigungen (falls nicht schon vorhanden)

**Aufwand:** ~2-3 Stunden (Umbau bestehender Logik + Spieler-Channel-Nachricht)
**Dateien:** `cogs/scheduler_cog.py`, `modules/notifications/discord_notifier.py`
**Abhaengigkeiten:** Keine neuen

### 21. MC Ankuendigungs-Banner (`/mc say` Erweiterung + In-Game)

Erweiterung des bestehenden `/mc say`-Befehls: Statt nur einer Chat-Nachricht (`say`) wird zusaetzlich ein grosser Ankuendigungsbanner auf dem Bildschirm aller Spieler angezeigt. Funktioniert sowohl aus Discord heraus als auch In-Game.

**Aktuelles Verhalten (v3.1.0):**
- `/mc say <nachricht>` sendet `say <nachricht>` per RCON
- Nachricht erscheint nur im Chat (leicht zu uebersehen)
- Kein visueller Banner auf dem Bildschirm

**Neues Verhalten:**
- `/mc say <nachricht>` sendet drei RCON-Befehle:
  1. `title @a title {"text":"Ankuendigung","color":"gold","bold":true}` — Grosser Titel-Text auf dem Bildschirm
  2. `title @a subtitle {"text":"<nachricht>","color":"white"}` — Untertitel mit der eigentlichen Nachricht
  3. `say [Ankuendigung] <nachricht>` — Zusaetzlich im Chat (fuer Spieler die den Banner verpassen)
- Optional: `title @a times 20 100 40` vorher senden (Fade-In 1s, Anzeige 5s, Fade-Out 2s)
- Zusaetzlich: `title @a actionbar {"text":"<nachricht>","color":"yellow"}` — Actionbar-Text bleibt laenger sichtbar als Title
- Neuer optionaler Parameter: `/mc say <nachricht> [banner:true/false]` — Banner kann deaktiviert werden fuer reine Chat-Nachrichten
- Standard: Banner = true

**Wiederholungs-Modus (fuer Restart-Warnungen):**
- Neuer optionaler Parameter: `/mc say <nachricht> [repeat:X]` — Nachricht wird alle 30 Sekunden X-mal wiederholt
- Bei Repeat: Nur Actionbar + Chat (kein Title-Banner bei Wiederholungen, um nicht zu nerven)
- Countdown-Text: "Server-Restart in X Minuten" → automatisch herunterzaehlen
- Anwendungsfall: `/mc say "Server-Restart in 5 Minuten" repeat:10` → 10x alle 30s = 5 Minuten Vorwarnung
- Background-Task fuer die Wiederholungen (asyncio Timer, bricht ab wenn Server gestoppt wird)

**In-Game-Ausloesung (Minecraft Datapack/Plugin):**
- Falls gewuenscht: Datapack das bei einem bestimmten Chat-Trigger (z.B. `!announce <text>`) den gleichen Title-Banner ausfuehrt
- Alternative: Per RCON-Bridge — Spieler mit Admin-Rechten koennen `/say !announce` schreiben, der Monitor Bot erkennt das im Log und fuehrt den Title-Befehl per RCON aus
- Erkennung im bestehenden Log-Parser (`process_mc_line`): Neues Pattern fuer `!announce`-Trigger

**Technische Aenderungen:**
- `cogs/minecraft_cog.py`: `mc_say()` erweitern — vor dem `say`-Befehl `title`-Befehle per RCON senden
- `title`-Texte als JSON-Formatierung (MC Raw JSON Text): Farbe, Bold, etc. konfigurierbar
- RCON-Sanitizer: `title`-Befehl in Whitelist aufnehmen (falls nicht schon erlaubt)
- Optional: `modules/monitoring/log_parser.py` erweitern fuer `!announce`-Trigger aus In-Game-Chat

**Aufwand:** ~1-2 Stunden
**Dateien:** `cogs/minecraft_cog.py`
**Abhaengigkeiten:** Keine neuen

### 22. MC Gameplay-Commands entfernen (nur In-Game)

Die Befehle `/mc difficulty`, `/mc weather`, `/mc time` und `/mc gamemode` werden aus den Discord Slash-Commands entfernt. Diese Gameplay-Einstellungen sollen ausschliesslich In-Game per Minecraft-Befehlen verfuegbar sein — sie haben in Discord nichts zu suchen.

**Entfernte Discord-Commands:**
- `/mc difficulty <level> [server]` → entfernen (In-Game: `/difficulty <level>`)
- `/mc weather <typ> [server]` → entfernen (In-Game: `/weather <typ>`)
- `/mc time <wert> [server]` → entfernen (In-Game: `/time set <wert>`)
- `/mc gamemode <modus> [spieler] [server]` → entfernen (In-Game: `/gamemode <modus> [spieler]`)

**Beibehalten in Discord:**
- `/mc say` — bleibt (wird zu Ankuendigungs-Banner erweitert, Feature #21)
- Alle anderen MC-Commands (players, backup, config, whitelist, blacklist, etc.) — bleiben

**Technische Aenderungen:**
- `cogs/minecraft_cog.py`: 4 Command-Methoden entfernen (`mc_difficulty`, `mc_weather`, `mc_time`, `mc_gamemode`)
- Kommentar-Header aktualisieren (Zeile 14 und Zeile 862)
- Keine Auswirkung auf andere Module

**Aufwand:** ~30 Minuten (reines Entfernen)
**Dateien:** `cogs/minecraft_cog.py`

### 23. MC IP-Ban (wie SAT)

Erweiterung des Minecraft-Bans um IP-basierte Sperrung per UFW-Firewall — identisch zum bestehenden SAT-Ban-System.

**Aktuelles Verhalten (v3.1.0):**
- `/mc players ban <spieler>` sendet nur `ban <spieler>` per RCON
- MC speichert das in `banned-players.json` (Name/UUID-basiert)
- Spieler kann mit neuem Account wieder joinen
- `player_ip_tracker` trackt bereits MC-Spieler-IPs aus Logs (Parser vorhanden)
- Die generische `ban_player()`-Methode mit UFW-Block existiert, wird aber beim MC-Ban nicht genutzt

**Neues Verhalten:**
- `/mc players ban <spieler>` fuehrt beides aus:
  1. RCON `ban <spieler>` (MC-interner Name/UUID-Ban — bleibt bestehen)
  2. `player_ip_tracker.ban_player()` (UFW IP-Block — wie bei SAT)
- Doppelte Sicherheit: Auch wenn einer der Mechanismen umgangen wird, greift der andere
- `/mc players pardon <spieler>` hebt ebenfalls beides auf: RCON `pardon` + `ip_tracker.unban_player()`
- Wenn keine IP bekannt → nur RCON-Ban, Warnung dass IP-Ban nicht moeglich war
- Blacklist-System (Phase 8e): Ergaenzen um IP-Feld — bei `blacklist add` ebenfalls UFW-Block setzen

**Ban-Umgehungs-Erkennung:**
- Bereits implementiert im Monitor Bot: Wenn eine gebannte IP mit neuem Namen connectet → Warnung im Admin-Channel
- Funktioniert jetzt auch fuer MC (gleicher `player_ip_tracker`)

**Technische Aenderungen:**
- `cogs/minecraft_cog.py`: `mc_ban()` und `mc_pardon()` um `ip_tracker`-Aufrufe erweitern
- `cogs/minecraft_cog.py`: `player_ip_tracker`-Instanz muss im Cog verfuegbar sein (analog zu SAT)
- `modules/minecraft/blacklist.py`: Optional IP-Feld + UFW-Block bei Blacklist-Eintraegen
- Keine neuen Abhaengigkeiten — alle Komponenten existieren bereits

**Aufwand:** ~1-2 Stunden (Verdrahtung bestehender Komponenten)
**Dateien:** `cogs/minecraft_cog.py`, optional `modules/minecraft/blacklist.py`
**Abhaengigkeiten:** Keine neuen (player_ip_tracker + UFW bereits vorhanden)

### 24. Timeout-System Erweiterung (Temporaerer Server-Ban + Restzeit)

Kompletter Umbau des Timeout-Systems: Statt nur Game-Kick + Discord-Timeout wird der Spieler temporaer von ALLEN Gameservern ausgesperrt. Nach Ablauf wird der Zugang automatisch wiederhergestellt. Spieler koennen ihre verbleibende Timeout-Zeit jederzeit abfragen.

**Aktuelles Verhalten (v3.1.0):**
- `/timeout <spieler> <dauer_min> [grund]` kickt nur vom SAT-Server (KickPlayer) + Discord-Timeout
- Kein temporaerer Ban — Spieler kann sofort wieder joinen
- Kein Ban auf MC-Servern
- Keine Restzeit-Abfrage
- Nur SAT, kein Multi-Server-Support

**Neues Verhalten:**

Timeout setzen (Admin):
- `/timeout <spieler> <dauer> [grund]` sperrt auf ALLEN Gameservern gleichzeitig:
  1. **Discord-Timeout** setzen (wie bisher)
  2. **SAT:** IP-Ban per UFW (wie `/sat players ban`, nutzt `player_ip_tracker`)
  3. **MC (BMC + Vanilla):** RCON `ban <spieler>` + IP-Ban per UFW (Feature #23)
  4. **Eintrag in Timeout-Datenbank** mit Ablaufzeit
- Spieler wird per DM benachrichtigt: "Du wurdest fuer X Stunden von allen Servern gesperrt. Grund: ..."
- Optional: Nur bestimmte Server (Parameter `[server:alle/sat/mc/bmc/vanilla]`, Standard: alle)

Automatisches Aufheben (Background-Task):
- Neuer Background-Task im Monitor Bot: Prueft alle 60 Sekunden ob Timeouts abgelaufen sind
- Bei Ablauf:
  1. SAT: IP-Ban per UFW aufheben (`player_ip_tracker.unban_player()`)
  2. MC: RCON `pardon <spieler>` + UFW-Unblock
  3. Discord-Timeout wird automatisch von Discord aufgehoben (eingebautes Feature)
  4. Eintrag als "abgelaufen" markieren
- Spieler wird per DM benachrichtigt: "Dein Timeout ist abgelaufen. Du kannst wieder auf allen Servern spielen."
- Benachrichtigung im konfigurierten Mod-Channel (Benachrichtigungs-Routing)

Restzeit abfragen (Spieler + Admin):
- `/timeout status` — Zeigt die eigene verbleibende Timeout-Zeit an (funktioniert immer, auch waehrend Timeout)
- Wenn kein Timeout aktiv: "Du hast keinen aktiven Timeout."
- Wenn Timeout aktiv: Embed mit Restzeit (Countdown), Grund, gesperrt seit, gesperrt bis, betroffene Server
- `/timeout status <spieler>` — Admins koennen die Timeout-Info anderer Spieler abfragen
- `/timeout status all` — Admin-Uebersicht aller aktiven Timeouts (Tabelle: Spieler, Restzeit, Grund, betroffene Server)

Timeout-Channel (optional):
- Dedizierter Discord-Channel `#timeout-info` der nur fuer getimoutete Spieler sichtbar ist
- Bei Timeout: Spieler bekommt Zugang zum Channel (Permission-Overwrite)
- Im Channel: Bot postet automatisch ein Embed mit Restzeit, Grund, Regeln
- Bei Ablauf: Zugang wird wieder entzogen
- Zweck: Spieler koennen dort ihre Restzeit sehen und haben einen Anlaufpunkt
- Channel-ID konfigurierbar im Dashboard (Benachrichtigungs-Routing oder Config-Panel)

Vorzeitiges Aufheben (Admin):
- `/timeout aufheben <spieler>` — Timeout manuell aufheben (auf allen Servern sofort entbannen)
- Fuehrt sofort aus: UFW-Unblock (SAT + MC), RCON pardon (MC), Discord-Timeout aufheben
- Spieler wird per DM benachrichtigt: "Dein Timeout wurde vorzeitig aufgehoben."
- Eintrag in Timeout-Historie als "vorzeitig aufgehoben von [Admin]"
- Dashboard: Button in der Spielerliste zum Aufheben

Timeout-Historie:
- `/timeout list` — Alle aktiven Timeouts anzeigen (Admin)
- `/timeout history <spieler>` — Vergangene Timeouts eines Spielers (Admin)
- Persistenz: `data/timeouts.json` (oder `data/admin/timeouts.json` im Admin Bot)

**Technische Aenderungen:**
- `cogs/timeout_cog.py`: Komplett umbauen — Multi-Server-Support, Persistenz, Background-Task
- Neues Modul `modules/timeout_manager.py`: Timeout-Logik, Datenbank, automatisches Aufheben
- Integration mit `player_ip_tracker` (SAT + MC) fuer IP-Bans
- Integration mit MC RCON fuer Name-Bans
- Background-Task: `check_expired_timeouts()` alle 60s
- Timeout-Channel: `on_timeout_set` → Permission-Overwrite setzen, `on_timeout_expire` → entfernen
- Datenstruktur pro Timeout: `{spieler, discord_id, grund, gesetzt_von, gesetzt_am, ablauf, server_list, aktiv, ip}`

**Migration:**
- Timeout wandert in den Admin Bot (Feature #18) — da dort die Moderations-Features gebuendelt sind
- Bis dahin: Bleibt im GameServer Bot, aber mit erweiterter Funktionalitaet

**Aufwand:** ~4-6 Stunden
**Dateien:** `cogs/timeout_cog.py` (Umbau), neues `modules/timeout_manager.py`
**Abhaengigkeiten:** Feature #23 (MC IP-Ban), `player_ip_tracker` (bereits vorhanden)

### 25. Command-Aufraeumung (Loeschen, Umbenennen, Dashboard-Migration)

Grosse Aufraeumaktion der Discord Slash-Commands. Viele Befehle wandern ins Dashboard, einige werden umbenannt, einige komplett entfernt. Ziel: Nur noch die wirklich in Discord sinnvollen Commands behalten.

**SAT — Loeschen (Funktionen wandern ins Dashboard):**
- `/sat start` → Dashboard Quick-Action
- `/sat stop` → Dashboard Quick-Action
- `/sat restart` → Dashboard Quick-Action
- `/sat cancel` → Dashboard Quick-Action
- `/sat backup create` → Dashboard Backup-Tab
- `/sat config playerlimit` → Dashboard Server-Config

**SAT — Umbenennen (backup/config → sav):**
Neue Command-Gruppe `/sat sav` fuer Savegame-bezogene Befehle:
- `/sat backup download` → `/sat sav download`
- `/sat backup list` → `/sat sav list`
- `/sat backup restore` → `/sat sav restore`
- `/sat backup save` → `/sat sav save`
- `/sat config load` → `/sat sav load`
- `/sat config stats` → `/sat sav stats`

**SAT — Komplett loeschen:**
- `/sat config autosave` → nicht mehr noetig (Dashboard oder Server-Setting)
- `/sat config console` → ins Dashboard als RCON-aehnliche Konsole (oder entfallen, da nur Owner braucht)
- `/sat config update` → nicht mehr noetig (Auto-Update Feature #20)
- `/sat config settings` → ins Dashboard (Server-Detail → Config-Tab)
- `/sat config settings_backup` → ins Dashboard (Server-Detail → Config-Tab)
- `/sat config settings_restore` → ins Dashboard (Server-Detail → Config-Tab)

**MC — Loeschen (Funktionen wandern ins Dashboard):**
- `/mc start` → Dashboard Quick-Action
- `/mc stop` → Dashboard Quick-Action
- `/mc restart` → Dashboard Quick-Action
- `/mc cancel` → Dashboard Quick-Action
- `/mc config autosave` → Dashboard Server-Config
- `/mc config set` → Dashboard Server-Config

**Allgemein — Loeschen:**
- `/server` → nicht mehr noetig (Dashboard Uebersicht zeigt alles)

**Allgemein — Ins Dashboard migrieren (aus Discord entfernen):**
- Alle `/maint` Befehle (Maintenance-Mode) → Dashboard-Buttons pro Server
- `/mod install`, `/mod uninstall`, `/mod update`, `/mod search`, `/mod export`, `/mod import` → Dashboard Mod-Verwaltung
- `/mod list` und `/mod info` → **bleiben in Discord** (Spieler-sichtbar, nur lesend)

**Verbleibende Discord-Commands nach Aufraeumung:**

SAT:
- `/sat status` (Alle) — schneller Status-Check
- `/sat players online|ban|unban|bans` (Spieler/Admin) — Spieler-Verwaltung
- `/sat sav download|list|restore|save|load|stats` (Spieler/Admin/Owner) — Savegame-Zugriff
- `/sat blueprints upload|list|download|delete` (Spieler/Admin) — Blueprints
- `/sat whitelist add|remove|list` (Admin) — Whitelist
- `/sat blacklist add|remove|list` (Admin) — Blacklist

MC:
- `/mc status [server]` (Alle) — schneller Status-Check
- `/mc players list|kick|ban|pardon [server]` (Spieler/Admin) — Spieler-Verwaltung
- `/mc backup list|download [server]` (Spieler/Admin) — Backup-Zugriff (create/restore → Dashboard)
- `/mc whitelist add|remove|list [server]` (Admin) — Whitelist
- `/mc blacklist add|remove|list [server]` (Admin) — Blacklist
- `/mc say [server]` (Admin) — Ankuendigungs-Banner (Feature #21)
- `/mc config settings|update [server]` (Spieler/Owner) — Status-Info + Paper-Update-Check

Allgemein:
- `/help` — Rollenbasiert (Feature #25)
- `/timeout` — Erweitertes System (Feature #24)
- `/performance` — System-Infos (Spieler)
- `/stats` — Spieler-Statistiken (Spieler)
- `/report` — Berichte (Spieler)
- `/mod list|info` — Mod-Infos (Spieler, nur lesend)
- `/clear` — Nachrichten loeschen (Admin)
- `/reload` — Cog neuladen (Owner)
- `/ping` → entfaellt (Dashboard Bot-Status-Leiste)

**Technische Aenderungen:**
- `cogs/satisfactory_cog.py`: start/stop/restart/cancel + config-Commands entfernen, backup → sav umbenennen
- `cogs/minecraft_cog.py`: start/stop/restart/cancel + config set/autosave entfernen
- `cogs/general_cog.py`: `/server` und `/ping` entfernen
- `cogs/mod_cog.py`: install/uninstall/update/search/export/import entfernen (list + info bleiben)
- Maintenance-Cog: Alle Commands entfernen (komplett ins Dashboard)
- `cogs/timeout_cog.py`: Umbau gemaess Feature #24

**Aufwand:** ~3-4 Stunden (viel Loeschen, etwas Umbenennen, Help aktualisieren)
**Dateien:** Alle Cog-Dateien, `cogs/general_cog.py` (Help aktualisieren)
**Abhaengigkeiten:** Feature #13 (Dashboard muss die Funktionen uebernehmen)

### 26. Rollenbasierter Help-Befehl

Der `/help`-Befehl zeigt aktuell alle Commands an, auch solche die der Nutzer nicht verwenden darf. Neues Verhalten: Nur Commands anzeigen, fuer die der Nutzer die Berechtigung hat.

**Aktuelles Verhalten (v3.1.0):**
- `/help` zeigt ein hardcodiertes Embed mit allen ~110 Commands
- Jeder sieht alles (auch Owner-Commands wie `/reload`, `/config console`)
- Berechtigungsstufen stehen als Text am Ende: "Owner > Admin > Spieler > Alle"

**Neues Verhalten:**
- `/help` prueft die Rolle des aufrufenden Users (`is_owner()`, `is_admin()`, `is_spieler()`)
- Nur Commands anzeigen, die der User auch ausfuehren darf:
  - **Alle:** `/help`, `/server`, `/sat status`, `/sat players online`, `/performance`, `/stats`, `/report`
  - **Spieler-Rolle:** Zusaetzlich `/sat config settings`, `/sat config stats`, `/sat backup list`, `/mc ...` (Spieler-Commands)
  - **Admin-Rolle:** Zusaetzlich `/sat start`, `/stop`, `/restart`, `/sat players ban`, `/backup create`, `/clear`, etc.
  - **Owner:** Alle Commands inkl. `/reload`, `/ping`, `/sat config console`, `/sat config update`, Restore-Commands
- "(Admin)" und "(Owner)" Markierungen bei Commands die hoehere Rechte brauchen (sichtbar fuer Admins/Owner)
- Command-Gruppen ohne sichtbare Commands werden komplett ausgeblendet

**Technische Aenderungen:**
- `cogs/general_cog.py`: `help_cmd()` umbauen — dynamisch basierend auf `is_owner(interaction)`, `is_admin(interaction)`, `is_spieler(interaction)`
- Command-Listen als Datenstruktur (Dict/List mit Permission-Level pro Command) statt hardcoded Strings
- Embed-Felder nur hinzufuegen wenn mindestens ein sichtbarer Command in der Gruppe ist

**Aufwand:** ~1-2 Stunden
**Dateien:** `cogs/general_cog.py`
**Abhaengigkeiten:** Keine neuen (nutzt bestehende `utils/permissions.py` Funktionen)

---

## Zusammenfassung

| # | Feature | Prio | Aufwand | Status |
|---|---------|------|---------|--------|
| 11 | MC World-Analyse | P2 | 4-5h | Geplant |
| 13 | Web-Dashboard (inkl. #14 Config-Panel) | P3 | 25-35h | Geplant |
| 16 | TeamSpeak-Integration (3 Phasen) | P3 | 15-22h | Geplant (Admin Bot) |
| 17 | Discord Temp Voice Channels | P2 | 6-8h | Geplant (Admin Bot) |
| 18 | Admin Bot — Maki-Ersatz (Moderation, Roles, Leveling, Tickets, Logging, Giveaways) | P3 | 20-30h | Geplant |
| 19 | Discord + TS Server-Backup (Struktur-Snapshot) | P2 | 8-12h | Geplant (Admin Bot) |
| 20 | SAT Auto-Update (sofort bei leerem Server + Spieler-Benachrichtigung) | P2 | 2-3h | Geplant |
| 21 | MC Ankuendigungs-Banner (/mc say + In-Game) | P2 | 1-2h | Geplant |
| 22 | MC Gameplay-Commands entfernen (nur In-Game) | P2 | 0.5h | Geplant |
| 23 | MC IP-Ban wie SAT (UFW-Firewall) | P2 | 1-2h | Geplant |
| 24 | Timeout-System (Temp-Ban alle Server + Restzeit + Channel) | P2 | 4-6h | Geplant |
| 25 | Command-Aufraeumung (Loeschen, Umbenennen, Dashboard-Migration) | P2 | 3-4h | Geplant |
| 26 | Rollenbasierter Help-Befehl | P2 | 1-2h | Geplant |

### Erledigte Features (v3.1.0)

| # | Feature | Phase |
|---|---------|-------|
| 1 | Web-Status-Seite (statisch) | Phase 8g |
| 2 | Scheduled Messages | Phase 8f |
| 3 | Backup-Statistiken | Phase 8c |
| 4 | Server-Offline Decorator | Phase 8a |
| 6 | BMC Modpack-Updates | Phase 8h |
| 8 | Config-Backup Rotation + GPG | Phase 8d |
| 10 | MC Blacklist-System | Phase 8e |
| 12 | MC Autosave-Command | Phase 8b |

---

### Feature-Parity Uebersicht (SAT vs MC)

| Feature | SAT | MC | Status |
|---------|-----|----|----|
| Server-Steuerung (start/stop/restart) | Ja | Ja | Parity |
| Spieler-Management (kick/ban) | Ja (IP-basiert) | Ja (Name + IP, Feature #23) | Parity |
| Backup create/list/restore/download | Ja | Ja | Parity |
| Config backup/restore | Ja | Ja | Parity |
| Whitelist | Ja | Ja | Parity |
| Blacklist (eigenes System) | Ja | Ja (v3.1.0) | Parity |
| Blueprint-System | Ja | N/A | Nicht anwendbar fuer MC |
| Savegame/World-Statistiken | Ja (detailliert) | Ja (einfach) | Feature #11 |
| Update-Checker | Ja (SteamCMD) | Ja (Paper API + Modpack) | Parity |
| Auto-Update | Ja (bei leerem Server, Feature #20) | Nein (nur Benachrichtigung) | SAT-exklusiv |
| Settings-Management | Ja | Ja | Parity |
| Autosave-Command | Ja | Ja (v3.1.0) | Parity |
| Admin-Commands (weather/time/etc.) | N/A | Nur In-Game (Feature #22) | MC-exklusiv |
| Multi-Server | Nein | Ja | MC-exklusiv |
