# Discord Bot System v4.5.0

**3-Bot-System + Web-Dashboard fuer Game-Server- UND Community-Management**

Satisfactory + Minecraft Server-Management mit Discord-Integration, Web-Dashboard und automatisiertem Monitoring — erweitert um ein Community-Toolkit (Leveling, Temp-Voice, LFG, Moderation, Verification) auf Multi-Tenant-Fundament (alles per-Guild, Daten-isoliert, Dashboard-konfigurierbar).

Server: Netcup RS 4000 G12 (12 vCores, 31 GB RAM, 1 TB NVMe) | Ubuntu 22.04 LTS | Python 3.10

> **Stand 2026-08-15.** Zwei Neuerungen praegen den aktuellen Aufbau:
> **(1)** Satisfactory laeuft in **mehreren Instanzen** (heute zwei), und welche
> es gibt, steht nicht mehr im Code, sondern in einer ENV-Variablen.
> **(2)** Die Bots heissen seit dem 13.06.2026 **recon**, **operator** und
> **marshal** — die alten Namen (monitor-, gameserver-, admin-bot) tauchen nur
> noch in historischen Dokumenten auf.

---

## Architektur

```
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│     operator-bot     │  │      recon-bot       │  │     marshal-bot      │
│   (ex gameserver)    │  │    (ex monitor)      │  │     (ex admin)       │
│                      │  │                      │  │                      │
│  /sat  (25 Befehle,  │  │  Health Auto-Restart │  │  Moderation (Warn/   │
│    je Instanz)       │  │  Service Watchdog    │  │    Ban/Timeout)      │
│  /mc   (22 Befehle)  │  │  Disk Guard          │  │  Leveling + XP       │
│  /mod /todo /design  │  │  Port Monitor        │  │  Giveaways           │
│  /timeout /help      │  │  SSL/DuckDNS/Fail2Ban│  │  Tickets             │
│                      │  │  Stats Collector     │  │  Custom Commands     │
│  Chat Bridge         │  │  Crash Replay        │  │  Reaction Roles      │
│                      │  │  Scheduler + Reports │  │  Audit Logging       │
│  9 Top-Level-Befehle │  │  24 Top-Level        │  │  24 Top-Level        │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘
                                     │
                        ┌─────────────┴─────────────┐
                        │      Web Dashboard        │
                        │                           │
                        │  Server-Status + Control  │
                        │  Analytics + Forecasting  │
                        │  Security Dashboard       │
                        │  Config-Editor            │
                        │  WebSocket Live-Updates   │
                        │  Dark Mode                │
                        │  Export (CSV/JSON)         │
                        └───────────────────────────┘
```

Ein vierter Bot, **pipeline-bot**, bedient die Content-Pipeline. Er hat keine
Slash-Befehle, sondern arbeitet ueber Knoepfe und Ereignisse.

---

## Mehrere Server derselben Sorte — die Registry

Frueher stand im Code, welche Server es gibt. Heute steht es in zwei
ENV-Variablen, und der Code fragt eine Registry (`modules/server_registry.py`):

```bash
SAT_SERVER_IDS=MAIN,SECOND     # zwei Satisfactory-Instanzen
MC_SERVER_IDS=BMC              # ein Minecraft-Server
```

Daraus folgt alles Weitere: Auswahllisten der Slash-Befehle, Anzeigenamen,
Statusdateien, Dashboard-Kacheln, Portzuordnung, Watchdog-Liste, Backup-Ziele.
Je Instanz gibt es einen eigenen ENV-Block (`SAT_MAIN_*`, `SAT_SECOND_*`).

Praktische Folge — beides ohne eine einzige geaenderte Codezeile:

| Ich will… | Das reicht |
|---|---|
| eine Instanz abschalten | ID aus `SAT_SERVER_IDS` entfernen, Bots neu starten |
| eine dritte dazu | ID ergaenzen, `SAT_THIRD_*`-Block anlegen, Bots neu starten |
| Minecraft Vanilla zurueck | `MC_SERVER_IDS=BMC,VANILLA`, ENV-Block einkommentieren |

Befehle nehmen einen optionalen `server`-Parameter mit Autovervollstaendigung.
Ohne Angabe gilt die erste Instanz — die eingeuebten Befehle funktionieren also
unveraendert weiter.

**Ausnahme, bewusst:** Whitelist und Blacklist sind datenbankgestuetzt und
gelten spielweit, nicht je Instanz. Diese sechs Befehle haben deshalb keinen
`server`-Parameter und sagen das in ihrer Beschreibung.

---

## Feature-Highlights

### Satisfactory mehrinstanzfaehig (v4.5.0, 2026-08-15)

Der Umbau betraf nicht nur die Serververwaltung, sondern jede Stelle, an der ein
Server *gemeint* war:

- **19 von 25 `/sat`-Befehlen** nehmen einen `server`-Parameter mit
  Autovervollstaendigung; die uebrigen sechs sind die spielweiten Listen
- **Bestaetigungs-Dialoge** loesen ihre Zielinstanz selbst auf, statt auf ein
  festes Objekt zuzugreifen
- **Statuspanel, Dashboard-Kacheln und Detailseiten** je Instanz
- **Absturz-Meldungen, Bann-Verteiler, Timeouts, Backups, Mod-Listen** je Instanz
- **Auto-Update** installiert auf jeder Instanz statt nur auf der ersten — ein
  Satisfactory-Server mit falschem Build nimmt keine Spieler an, der Ausfall
  haette also nicht nach „Update vergessen" ausgesehen, sondern nach „kaputt"
- **Tick-Ampel** gegen den echten Sollwert 60 (vorher stillschweigend gegen 30
  kalibriert: ein Server bei halber Geschwindigkeit wurde gruen angezeigt) —
  faellt die Rate unter 50, meldet der Bot es
- **Blaupausen:** die aktive Welt wird beim Server erfragt statt aus Dateizeiten
  geraten, und nach einem Upload startet der Server selbsttaetig neu
  (5-Minuten-Countdown, abbrechbar mit `/sat cancel`). Vorher stand dort nur ein
  Knopf — wurde er nicht gedrueckt, blieb der Upload wirkungslos, weil der
  Server Blaupausen nur beim Start einliest.

### Community-Rebuild (v4.4.0 — Multi-Tenant + Community-Toolkit)

Grossumbau zum lebendigen Community-Server auf Multi-Tenant-Fundament (alles per-Guild, Daten-isoliert, Dashboard-konfigurierbar):

- **Multi-Tenant-Fundament** (`modules/guild_context.py`): per-Guild Settings + Feature-Flags + Guild-Registry, 15s-TTL-Cache. Keine `GUILD_ID`-Hardcodes mehr.
- **Leveling-Rebuild (Arcane-Tier):** echte Voice-Sessions (Anti-Cheat: >=2 Menschen, kein self-deaf/AFK), No-XP-Channels, Leaderboard-Pagination + Pillow-Bild-Karte, Role-Rewards, Web-Leaderboard mit Namen + Avataren — komplett Dashboard-konfigurierbar.
- **Temp-Voice Multi-Hub:** mehrere Join-to-Create-Hubs, je eigene Kategorie/Naming-Template (`{user}`/`{count}`/`{game}`)/Limit/Privat. 9-Button-Control-Panel. Dashboard + Slash.
- **LFG** (`#spielersuche`): selbst-zuweisbare Ping-Rolle, `/lfg ping`/`an`/`aus`/`panel`.
- **Moderation + AutoMod:** Word-Filter, Anti-Spam, Invite-Filter, Mass-Caps, Zalgo — alle per-Guild im Dashboard schaltbar.
- **Verification-Gate + Raid-Detection** (Anti-Bot/Anti-Raid) + **Linked-Accounts** (`/link`).
- **Dashboard-Realtime:** SSE → **WebSocket** (auth-gated Push alle 5s).
- **In Arbeit:** RBAC (Dashboard-Rollen + Audit-Log), Music (Lavalink), neue Web-Pages (Landing/Welcome).

Details: `CHANGELOG.md` -> `[Unreleased]`.

### Sicherheit + Stabilitaet (Phase 1)
Pre-Boot Selftest, Graceful Shutdown, CSRF-Schutz, Session-Timeout, Health Auto-Restart mit UDP/TCP-Probes, Disk Guard (3-Stufen Warnung), Service Watchdog, DuckDNS Monitor, Port Monitor, Fail2Ban-Monitoring, SSL-Zertifikat-Monitor, Backup-Integritaetspruefung, Health-Route API und Rate-Limiter.

### SQLite-Datenbank (Phase 2)
Migration von JSON auf SQLite mit aiosqlite WAL-Modus (31 Tabellen), automatische Retention/Cleanup, Backup-Rotation und Config-Versionierung.

### Dashboard-Erweiterungen (Phase 3)
SSE Live-Updates, Korrelations-Dashboard, CSV/JSON-Export, Ressourcen-Forecasting, Error-Dashboard, FTS5-Volltextsuche, Stats-Collector, Analytics-Dashboard mit Heatmaps und Changelog-Seite.

### Bot-Erweiterungen (Phase 4)
Crash-Replay, Moderation mit Warn-System, Leveling/XP mit Leaderboard, Giveaway-System, Custom Commands, Alert-Deduplizierung und Graceful Degradation.

### Polishing (Phase 5)
Maintenance-Mode, Paket-Checker, Dark Mode, Webhook-Integration und Performance-Optimierung.

### Auto-Update System (I1–I9, v4.2.0)
Vollautomatischer Update-Flow fuer Minecraft-Server mit Crash-Recovery:
- **Phase 0**: Crash-Recovery beim Bot-Start (abgebrochene Updates fortsetzen)
- **Phase 1**: Erkennung neuer Versionen via CurseForge-API
- **Phase 2**: 10-Min-Countdown-Ankuendigung via In-Game-Chat
- **Phase 3**: Vorbereitung (Disk-Check, HAR-Suppress, Server-Stop)
- **Phase 4**: Backup (World + mods/config Rollback)
- **Phase 5**: Custom-Dateien sichern
- **Phase 6**: Update (Download, SHA1+MD5-Hash-Check, Extract, NeoForge, Atomic Swap)
- **Phase 7**: Verifikation (3 Startversuche, RCON-Check)
- **Phase 8**: Benachrichtigungen (Discord, DM, E-Mail, In-Game)

Sicherheitsfeatures:
- `asyncio.Lock` pro Server (kein paralleles Update)
- 3-Versuche-System mit automatischem Rollback bei Fehlschlag
- Pinned Lockfile (`requirements-lock.txt`) fuer reproduzierbare Deployments
- RCON-Bind-Drift-Schutz (rcon.host=127.0.0.1 nach jedem Update enforced)
- Pre-Push-Hook mit `detect-secrets` (verhindert Secret-Leaks in git-History)

Slash-Commands: `/modpack` + `/update` (siehe `cogs/update_cog.py`).

### Sicherheits-Review 2026-04-30 (Etappe 1+2+3+4)
- Token-Leak entfernt + History-Cleanup-Plan dokumentiert
- 6 CVE-Pakete aktualisiert (aiohttp, pyjwt, python-dotenv, python-multipart, pip, setuptools)
- uvicorn auf 127.0.0.1 gebunden (Defense-in-Depth)
- nginx Security-Headers ergaenzt (HSTS, CSP, X-Frame-DENY)
- 74× Bandit B110 + 10× B608 + 12× B105/B324/B202 refaktoriert/annotiert
- Player-Privacy-Hashing in persistenten Logs (`utils/privacy.py`)
- Marco aus botuser-Group entfernt (Defense-in-Depth)
- journald MaxUse=500M + 30day Retention

---

## Server-Infrastruktur

| Parameter | Wert |
|-----------|------|
| Server | Netcup RS 4000 G12 |
| OS | Ubuntu 22.04.5 LTS |
| CPU | 12 vCores |
| RAM | 31 GB |
| Disk | 1 TB NVMe (1007 GB) |
| Python | 3.10.12 |
| Uptime-Ziel | 24/7 |

---

## Unterstuetzte Gameserver

Stand 2026-08-15, gegen `systemctl is-active` geprueft:

| Server | Kennung | Dienst | Typ | Steuerung | Zustand |
|--------|---------|--------|-----|-----------|---------|
| Satisfactory | `MAIN` | `satisfactory` | Dedicated Server (HTTPS-API, SteamCMD) | HTTP-API + systemd | laeuft |
| Satisfactory 2 | `SECOND` | `satisfactory2` | dito, eigene Installation + eigenes `HOME` | HTTP-API + systemd | laeuft |
| MC Better MC | `BMC` | `minecraft-bmc` | Fabric-Modpack | RCON + systemd | laeuft |
| MC Vanilla/Paper | `VANILLA` | `minecraft-vanilla` | Paper MC 1.21.4 | RCON + systemd | **stillgelegt** (`inactive`, `disabled`) |

Vanilla ist aus Code, Befehlen und Auswahllisten heraus, aber nicht geloescht:
Daten, Unit und ENV-Block bleiben, damit der Weg zurueck ein ENV-Eintrag ist.

### Wie zwei Satisfactory-Instanzen nebeneinander laufen

Beide laufen unter demselben Linux-Nutzer `satisfactory`, getrennt werden sie
durch drei Dinge:

- **eigenes `HOME`** (`/home/satisfactory/sat2`) — die Unreal-Engine loest
  `~/.config` daraus auf, dort liegen Speicherstaende und Einstellungen
- **eigenes Installationsverzeichnis** — sonst teilten sich beide dieselbe
  `FactoryGame.log`, die der Spieler-Parser und das Crash-Replay lesen
- **eigene Ports**, und zwar *vier*, nicht drei

Der vierte Port ist der, an dem es am 2026-08-15 im Betrieb geknallt hat:
**`-ReliablePort`** (TCP, Vorgabe 8888). Ueber ihn schickt der Server die
Metadaten des Spielstands, bevor der Spieler ueberhaupt einsteigt. Beide
Instanzen hatten ihn auf 8888 — der zweite Server nahm ihn dem ersten weg, und
Spieler bekamen beim Beitritt einen Timeout, waehrend jede Statusanzeige gruen
blieb. Seither hat die zweite Instanz `-ReliablePort=8889`.

---

## Ports-Uebersicht

Gegen `ufw status` und `ss -tlnp` geprueft (2026-08-15):

| Port | Protokoll | Dienst | Zugriff |
|------|-----------|--------|---------|
| 443 | TCP/HTTPS | Nginx → Web-Dashboard | extern |
| 8080 | TCP/HTTP | Web-Dashboard (uvicorn) | nur localhost (`127.0.0.1:8080`) |
| 4422 | TCP | SSH | extern (zusaetzlich ueber Tailscale) |
| 7777 | TCP+UDP | Satisfactory MAIN — Spiel + HTTPS-API | extern |
| 15000 | UDP | Satisfactory MAIN — Beacon | extern |
| 15777 | UDP | Satisfactory MAIN — Query | extern |
| 8888 | TCP | Satisfactory MAIN — Reliable Messaging | extern |
| 7778 | TCP+UDP | Satisfactory SECOND — Spiel + HTTPS-API | extern |
| 15001 | UDP | Satisfactory SECOND — Beacon | extern |
| 15778 | UDP | Satisfactory SECOND — Query | extern |
| 8889 | TCP | Satisfactory SECOND — Reliable Messaging | extern |
| 25566 | TCP | MC Better MC | extern |
| 25575 | TCP | MC RCON (BMC) | nur localhost (UFW `DENY`) |
| 25565 | TCP | MC Vanilla | **UFW offen, kein Dienst dahinter** |

Die letzte Zeile ist kein Tippfehler, sondern eine Altlast: die Freigabe fuer
Vanilla steht noch, der Dienst ist stillgelegt. Ein offener Port ohne Dienst ist
nicht ausnutzbar, aber er erzaehlt eine falsche Geschichte ueber das System.
Dasselbe gilt fuer die UFW-Freigabe von 8080, die ins Leere laeuft, weil
uvicorn auf `127.0.0.1` gebunden ist.

---

## Tech-Stack

| Kategorie | Technologie |
|-----------|-------------|
| Sprache | Python 3.10 |
| Discord | Discord.py 2.x |
| Web-Framework | FastAPI + uvicorn |
| Templates | Jinja2 + HTMX |
| Datenbank | aiosqlite (SQLite WAL-Modus) |
| Frontend | Chart.js, CSS Custom Properties |
| Echtzeit | WebSocket (auth-gated Push) |
| Reverse Proxy | Nginx + Let's Encrypt |
| Prozess-Management | systemd |
| Authentifizierung | Discord OAuth2 + JWT |

---

## Quick Start

```bash
# 1. Abhaengigkeiten
pip install -r requirements.txt      # requirements-lock.txt fuer exakte Staende

# 2. Konfiguration
cp config/.env.example config/.env
nano config/.env    # Tokens, IDs, etc. eintragen

cp config/config.example.json config/config.json   # optional
nano config/config.json    # Funktionsschalter, Schwellen, Zeitplan

# 3. Dienste einrichten
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now operator-bot recon-bot marshal-bot web-dashboard

# 4. Status pruefen
bash scripts/manage_bots.sh status

# 5. Tests
python -m pytest tests/ -q
```

Die Server-Grundeinrichtung (nginx, Zertifikate, Gameserver-Dienste) laeuft von Hand und ist
nicht Teil dieses Repositories. Alle Geheimnisse liegen ausschliesslich in `config/.env` —
per `.gitignore` ausgeschlossen; `config/.env.example` dokumentiert die erwarteten Variablen
ohne Werte. Ebenso bleibt `config/config.json` lokal — sie traegt Discord-IDs der zum
Dashboard zugelassenen Nutzer und Rollen. Versioniert ist `config/config.example.json` mit
leeren Listen; fehlt die Datei ganz, faellt der Loader auf Vorgaben zurueck
(`utils/config.py`), das System startet also auch ohne sie.

Ausfuehrliche Anleitung: siehe `docs/Projektdokumentation_v4.0.0.md`

---

## Dokumentation

| Dokument | Beschreibung |
|----------|-------------|
| `CHANGELOG.md` | Versionshistorie (inkl. `[Unreleased]` Community-Rebuild) |
| `docs/README.md` | Index aller Doku-Dateien |
| `docs/Projektdokumentation_v4.0.0.md` | Ausfuehrliche Basis-Projektdokumentation |
| `docs/TEMPVOICE_UPGRADE_PLAN.md` | Temp-Voice-Spec (VOICEPANEL-Style) |
| `docs/RBAC_SPEC_2026-06-04.md` | RBAC-Modell (Dashboard-Rollen + Audit-Log) |
| `CODE_DOKUMENTATION_DC_BOTS.md` | Ausfuehrliche Code- und Modulbeschreibung |

---

## Hinweise zum oeffentlichen Stand

Dieses Repository zeigt den Code eines real betriebenen Systems. Betriebsinterna sind bewusst
nicht enthalten: Serveradressen, Zugangsdaten, Konfigurationsschnappschuesse der Infrastruktur
(nginx, systemd-Drop-ins), Runbooks und interne Arbeitsnotizen. Beispieladressen in Code und
Dokumentation stammen aus den Dokumentationsbereichen nach RFC 5737 und zeigen auf kein echtes
Ziel; Discord-IDs in Beispielen sind Platzhalter.

Wer das System nachbauen will, findet in `config/.env.example` die vollstaendige Liste der
erwarteten Variablen und in `systemd/` die Dienstdefinitionen.

---

## Lizenz

[MIT](LICENSE) — Nutzung, Aenderung und Weitergabe sind erlaubt, auch kommerziell, solange
Copyright-Hinweis und Lizenztext erhalten bleiben. Ohne Gewaehrleistung und Haftung.

**Autor:** Marco
