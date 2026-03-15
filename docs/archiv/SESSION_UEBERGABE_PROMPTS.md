# Session-Uebergabe: Prompt-Optimierung Discord Bot System

> **Datum:** 15. Maerz 2026
> **Naechste Aufgabe:** Projektdokumentation + Feature-Plaene durchlesen, offene Punkte identifizieren
> **Projektordner:** `C:\Users\Marco\OneDrive\Dokumente\DIscord_Bots\`

---

## Was in dieser Session gemacht wurde

Drei Dateien wurden geschrieben/aktualisiert um die Arbeit zwischen Claude Code (Server/Coding) und Cowork (lokale Datei-Ops) sauber zu trennen:

### 1. CLAUDE.md — Fuer Claude Code (Server-Deployment)
Komplett neu geschrieben fuer den Auftrag: Auto-Update-System integrieren und deployen.
- 7 Integrations-Aufgaben (B1-B7): monitor_bot.py, Scheduler, Chat-Bridge, Spielererkennung, Notifier DM, SAT-Updater, Discord-Commands
- DB-Migration ist FERTIG (migrations.py CURRENT_VERSION=4, laeuft automatisch)
- Server-Setup-Befehle, Deployment-Workflow, Verifikation
- Dashboard-Port: 8080 intern, 443 extern via Nginx

### 2. COWORK.md — NEU fuer Cowork (lokale Arbeit)
Eigene Datei im Projekt-Root damit Cowork nicht CLAUDE.md liest.
- Klare Abgrenzung: kein Server-Zugriff, CLAUDE.md nicht lesen
- 5 offene Bugs aus frueheren Sessions (SAT CPU/RAM, Chart, RCON, MC Vanilla, Ports)
- 5 gelernte Lektionen aus BMC5-Migration
- Projekt-Struktur, technische Details, 10 Regeln

### 3. PROGRESS.md — Aktualisiert
Zeigt Auto-Update-Phase mit Checkliste. Verweist auf CLAUDE.md bzw. COWORK.md.

---

## Aktueller Projekt-Stand

### Abgeschlossen
- v4.0.0 Upgrade (39 Features F27-F65, Review: SEHR GUT)
- BMC5-Migration (NeoForge 1.21.1)
- Dashboard-Bugfixes (Spielererkennung, Kick/Ban, Versionen, Bot-Ping)
- Doku-Cleanup (CHANGELOG, README, Projektdoku, docs/archiv)
- DB-Migration v3→v4 in migrations.py (modpack_updates + server_versions)
- Auto-Update Kern-Module lokal geschrieben (5 Dateien, ~90 KB)
- FEATURE_PLAN_AUTO_UPDATE.md v1.4 (48 KB Spezifikation)

### In Arbeit / Offen
- Auto-Update-Module noch NICHT in Bots integriert (monitor_bot.py, scheduler_cog.py etc.)
- Noch NICHT auf Server deployed
- Chat-Bridge In-Game-Befehle noch nicht implementiert
- Discord-Commands (/mc modpack, /sat update cancel) noch nicht implementiert
- Server-Setup (Sudoers, Staging-Dir, ENV) noch nicht ausgefuehrt

### Bekanntes Problem dieser Session
Mehrfach sind Nachrichten (meine Antworten und Marcos Eingaben) im Chat verschwunden. Daher der Wechsel in einen neuen Chat.

---

## NAECHSTE AUFGABE (im neuen Chat)

**Ziel:** Die Projektdokumentation und alle Feature-Plaene durchlesen und eine saubere Liste erstellen was WIRKLICH noch offen ist. In dieser Session wurde festgestellt dass einige Punkte die als "offen" markiert waren schon laengst erledigt sind (z.B. DB-Migration, SAT-Updater perform_update, mehrere Bugfixes).

**Konkret durchzulesen:**
1. `docs/Projektdokumentation_v4.0.0.md` — Gesamtueberblick (52 KB)
2. `docs/FEATURE_PLAN_AUTO_UPDATE.md` — Auto-Update Spezifikation (48 KB, abschnittsweise!)
3. `PROGRESS.md` — Aktueller Fortschritt
4. `CLAUDE.md` — Was Claude Code als naechstes tun soll
5. `COWORK.md` — Was Cowork als naechstes tun soll

**Ergebnis soll sein:**
- Bereinigte Liste: Was ist WIRKLICH noch offen?
- CLAUDE.md und COWORK.md falls noetig korrigieren (veraltete/erledigte Punkte entfernen)
- PROGRESS.md auf den tatsaechlichen Stand bringen

---

## Wichtige Dateien (Referenz)

| Datei | Zweck |
|-------|-------|
| `CLAUDE.md` | Anweisungen fuer Claude Code (Server/Deployment) |
| `COWORK.md` | Anweisungen fuer Cowork (lokale Datei-Ops) |
| `PROGRESS.md` | Aktueller Arbeitsstand |
| `docs/FEATURE_PLAN_AUTO_UPDATE.md` | Auto-Update Spezifikation v1.4 |
| `docs/Projektdokumentation_v4.0.0.md` | Ausfuehrliche Projektdoku |
| `docs/REVIEW_v4.0.0.md` | Review-Report (SEHR GUT) |
| `docs/SESSION_DASHBOARD_FIXES.md` | Offene Dashboard-Bugs |
| `docs/SESSION_STAND_BMC5_MIGRATION.md` | BMC5-Migration + offene Punkte |
| `docs/DEPLOYMENT_BUGFIXES_2026-02-22.md` | Dashboard-Bugfix Deployment |
| `docs/TODO_SERVER.md` | Server-Aufgaben (Pycache, Temp-Files etc.) |

---

## Marcos Arbeitsweise (wichtig fuer Kontext)

- Nutzt **Claude Code** fuer Server-Arbeit (SSH, Deployment, Tests)
- Nutzt **Cowork** fuer lokale Datei-Ops (Code schreiben, Plaene, Bugfixes)
- Nutzt **Lyra v2.0 Prompt-System** (XML-Tags, Few-Shot, ruhige Sprache, 150-300 Woerter)
- Bevorzugt **Review-vor-Aenderung-Workflow** — erst analysieren, dann genehmigen, dann umsetzen
- Projektordner: `C:\Users\Marco\OneDrive\Dokumente\DIscord_Bots\`
- Server: Netcup RS 4000 G12, SSH Port 4422, Ubuntu 22.04, Python 3.10.12
