# Dokumentations-Index

Navigations-Übersicht aller Doku-Dateien. Projekt-Root: `../README.md` · Changelog: `../CHANGELOG.md` · Arbeitsanweisungen: `../CLAUDE.md`.

---

## Einstieg

| Datei | Inhalt |
|-------|--------|
| [`Projektdokumentation_v4.0.0.md`](Projektdokumentation_v4.0.0.md) | Ausführliche Basis-Projektdokumentation (Architektur, Setup, Features) |
| [`SERVER_INFO.md`](SERVER_INFO.md) | Server-Infrastruktur, Pfade, Service-Layout |
| [`DISCORD_KANAL_BESCHREIBUNGEN.md`](DISCORD_KANAL_BESCHREIBUNGEN.md) | Kanal-Topics + Server-Struktur |

## Specs & Feature-Pläne

| Datei | Inhalt |
|-------|--------|
| [`TEMPVOICE_UPGRADE_PLAN.md`](TEMPVOICE_UPGRADE_PLAN.md) | Temp-Voice-Spec (VOICEPANEL-Style, gelockt) |
| [`RBAC_SPEC_2026-06-04.md`](RBAC_SPEC_2026-06-04.md) | RBAC-Modell — Discord-Rollen → Dashboard-Perms, Audit-Log, Phasen R1–R5 |
| [`FEATURE_PLAN_DASHBOARD.md`](FEATURE_PLAN_DASHBOARD.md) | Dashboard-Feature-Spezifikation |
| [`FEATURE_PLAN_AUTO_UPDATE.md`](FEATURE_PLAN_AUTO_UPDATE.md) | Minecraft-Auto-Update-Flow (Phasen 0–8) |
| [`FEATURE_PLAN_N8N_DASHBOARD.md`](FEATURE_PLAN_N8N_DASHBOARD.md) | n8n-Dashboard-Integration (Plan) |

## Community-Rebuild (laufend, 2026-06)

| Datei | Inhalt |
|-------|--------|
| [`FRAGENKATALOG_community-rebuild_2026-06-04_v2.md`](FRAGENKATALOG_community-rebuild_2026-06-04_v2.md) | Offene Marco-Gates + Entscheidungen (v2 = aktuell) |
| [`HANDOFF_RBAC_2026-06-04.md`](HANDOFF_RBAC_2026-06-04.md) | Handoff RBAC R1–R3 für Parallel-Session |
| [`KONSOLIDIERUNG_2026-06-01.md`](KONSOLIDIERUNG_2026-06-01.md) | master↔main-Konsolidierung (v4.4.0) |

> **Roadmap + Tagesstand** liegen ausserhalb des Repos in `~/.claude/audit/` (PROGRESS-Files, WEBPAGES_PLAN) — projekt-lokale Arbeitsdateien, nicht versioniert.

## Production-Guides (`production/`)

Betriebs-Härtungs-Guides: `secret-management`, `security-headers`, `auth-hardening`, `database-hardening`, `backup-recovery`, `dependency-updates`, `observability`, `privacy-pii`, `server-config`, `memory-diagnostics`, `lavalink-setup` (Phase-E-Music-Vorbereitung).

## Betrieb & Historie

| Pfad | Inhalt |
|------|--------|
| `incidents/` | Post-Mortems (Symptom → Wurzelursache → Lesson) |
| `review/` · `reviews/` | Code-Review-Reports |
| `session_logs/` · `sessions/` | Session-Protokolle |
| `archiv/` | Abgeschlossene/veraltete Doku |
| [`OFFEN.md`](OFFEN.md) · [`ABGESCHLOSSEN.md`](ABGESCHLOSSEN.md) | Offene / erledigte Punkte |

---

> Konvention: Doku Deutsch, Code-Bezeichner Englisch, UTF-8. Neue Specs als eigenes `*.md` hier ablegen + in diesen Index aufnehmen.
