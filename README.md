# Discord Bot System v4.3.0

**3-Bot-System + Web-Dashboard fuer Game-Server-Management**

Satisfactory + Minecraft Server-Management mit Discord-Integration, Web-Dashboard und automatisiertem Monitoring.

Server: Netcup RS 4000 G12 (12 vCores, 31 GB RAM, 1 TB NVMe) | Ubuntu 22.04 LTS | Python 3.10

---

## Architektur

```
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│   GameServer Bot     │  │    Monitor Bot        │  │     Admin Bot        │
│                      │  │                       │  │                      │
│  /sat start/stop     │  │  Health Auto-Restart  │  │  Moderation (Warn/   │
│  /sat players/ban    │  │  Service Watchdog     │  │    Ban/Timeout)      │
│  /sat backup/save    │  │  Disk Guard           │  │  Leveling + XP       │
│  /mc status/start    │  │  Port Monitor         │  │  Giveaways           │
│  /mc players/backup  │  │  SSL/DuckDNS/Fail2Ban │  │  Tickets             │
│  Chat Bridge         │  │  Stats Collector      │  │  Custom Commands     │
│  General Commands    │  │  Crash Replay         │  │  Reaction Roles      │
│                      │  │  Daily Reports        │  │  Audit Logging       │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘
                                     │
                        ┌─────────────┴─────────────┐
                        │      Web Dashboard        │
                        │                           │
                        │  Server-Status + Control  │
                        │  Analytics + Forecasting  │
                        │  Security Dashboard       │
                        │  Config-Editor            │
                        │  SSE Live-Updates         │
                        │  Dark Mode                │
                        │  Export (CSV/JSON)         │
                        └───────────────────────────┘
```

---

## Feature-Highlights v4.0.0

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

| Server | Typ | Steuerung | Chat-Bridge |
|--------|-----|-----------|-------------|
| Satisfactory | Dedicated Server (HTTPS-API, SteamCMD) | HTTP API + systemd | — |
| MC Vanilla/Paper | Paper MC 1.21.4 | RCON + systemd | Log-Polling + RCON |
| MC Better MC | Fabric Modpack (BMC3) | RCON + systemd | Log-Polling + RCON |

---

## Ports-Uebersicht

| Port | Protokoll | Service | Zugriff |
|------|-----------|---------|---------|
| 443 | TCP/HTTPS | Nginx → Web-Dashboard | Extern |
| 8080 | TCP/HTTP | Web-Dashboard (uvicorn) | Nur localhost |
| 4422 | TCP | SSH | Extern |
| 7777 | TCP+UDP | Satisfactory Game | Extern |
| 15777 | UDP | Satisfactory Query | Extern |
| 25565 | TCP | MC Vanilla | Extern |
| 25566 | TCP | MC Better MC | Extern |
| 25575 | TCP | MC RCON (BMC) | Nur localhost |

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
| Echtzeit | Server-Sent Events (SSE) |
| Reverse Proxy | Nginx + Let's Encrypt |
| Prozess-Management | systemd |
| Authentifizierung | Discord OAuth2 + JWT |

---

## Quick Start

```bash
# 1. Server-Grundeinrichtung (als root)
bash scripts/setup_server.sh

# 2. Konfiguration
cp config/.env.example config/.env
nano config/.env    # Tokens, IDs, etc. eintragen

# 3. Deployment
bash scripts/deploy.sh

# 4. Status pruefen
bash scripts/manage_bots.sh status
```

Ausfuehrliche Anleitung: siehe `docs/Projektdokumentation_v4.0.0.md`

---

## Dokumentation

| Dokument | Beschreibung |
|----------|-------------|
| `docs/Projektdokumentation_v4.0.0.md` | Ausfuehrliche Projektdokumentation |
| `docs/FEATURE_PLAN.md` | Spezifikationen aller 39 Features (F27-F65) |
| `docs/REVIEW_v4.0.0.md` | Post-Upgrade Review-Report |
| `CHANGELOG.md` | Versionshistorie |
| `PROGRESS.md` | Upgrade-Fortschritt v3.2.0 → v4.0.0 |

---

## Lizenz

Privates Projekt — Kein oeffentliches Repository.

**Autor:** Marco
