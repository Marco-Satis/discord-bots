# Cowork-Aufgabe: Projektdokumentation v3.0.0 erstellen

## Kontext

Du arbeitest im Projektordner des Discord Bot Systems. Lies zuerst `CLAUDE.md` fuer den vollstaendigen Projektueberblick.

Das Projekt hat seit Version 2.2.0 massive Aenderungen erfahren:
- Komplette Minecraft-Integration (2 Server: Vanilla/Paper + Better MC)
- 18 Commits fuer Phase 14 (Minecraft)
- 78+ automatische Code-Fixes (Qualitaet, Sicherheit, Architektur)
- Bidirektionale Chat-Bridge, Health-Monitoring, Auto-Backups
- Multi-Server Architektur mit ENV-Prefix-Pattern

## Aufgaben (in dieser Reihenfolge)

### 1. VERSION aktualisieren
- Datei `VERSION` aendern: `2.2.0` → `3.0.0`

### 2. Neue Projektdokumentation erstellen

Erstelle `docs/Projektdokumentation_v3.0.0.md` basierend auf:
- `docs/Projektdokumentation_v2.2.0.docx` (bisherige Doku, lies sie vollstaendig)
- `docs/PHASE14_ZUSAMMENFASSUNG.md` (alle MC-Details)
- `docs/SESSION_STAND_BMC_SETUP.md` (aktueller MC-Server-Stand)
- `CHANGELOG_FIXES.md` (alle automatischen Fixes)
- `docs/REVIEW_BEFUNDE.md` (Review-Ergebnisse)
- `config/.env.example` (alle ENV-Variablen)

Die neue Doku soll enthalten:
- **Projektuebersicht** (aktualisiert: 3 Gameserver statt 1)
- **Architektur** (Zwei-Bot-System, alle Module, Minecraft-Erweiterungen)
- **Alle Commands** (Satisfactory + Minecraft, gruppiert nach Cog)
- **Minecraft-Integration** (Server-Setup, RCON, Chat-Bridge, Monitoring, Backups)
- **ENV-Variablen-Referenz** (komplett, inkl. aller MC_*-Variablen)
- **Server-Infrastruktur** (Hardware, Services, Ports, RAM-Aufteilung)
- **Deployment-Workflow** (SSH-Aliase, SCP, Service-Neustart)
- **Changelog v2.2.0 → v3.0.0** (alle Phasen zusammengefasst)

### 3. README.md aktualisieren

Aktualisiere `README.md` mit:
- Minecraft-Features prominent erwaehnen
- Aktualisierte Feature-Liste
- Command-Uebersicht (Satisfactory + Minecraft)
- Setup-Anleitung fuer MC-Server
- Verweis auf vollstaendige Doku

### 4. CHANGELOG.md erstellen

Erstelle `CHANGELOG.md` mit allen Aenderungen seit v2.2.0:
- v3.0.0: Minecraft-Integration, Code-Review-Fixes, Sicherheits-Fixes
- Gruppiert nach Typ (Features, Bug-Fixes, Sicherheit, Architektur)

## Regeln

- Sprache: Deutsch
- Keine Dateien in `config/.env`, `config/config.json`, `data/` anfassen
- Bestehende Dateien lesen aber nicht veraendern (ausser VERSION und README.md)
- Neue Dateien nur in `docs/` und Root erstellen
