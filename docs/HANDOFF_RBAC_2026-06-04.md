# HANDOFF — RBAC-Framework R1–R3 (Parallel-Session)

> **Für eine zweite Claude-Session.** Ziel: das **RBAC-Framework** bauen (Mechanik +
> Audit-Log + Mapping-UI) parallel zur Haupt-Session. **NICHT R4** (die konkrete
> Edit-Matrix je Bereich) — die entscheidet Marco separat. Voller Entwurf:
> `docs/RBAC_SPEC_2026-06-04.md` (zuerst lesen!).

---

## Koordination (WICHTIG — Kollisions-Schutz)

- **Eigener Feature-Branch:** `git checkout -b feature/rbac` (von `main`-HEAD). **NICHT auf `main` committen**, **NICHT deployen** — die Haupt-Session deployt/merged nach Review.
- **Diese Dateien NICHT anfassen** (gehören gerade der Haupt-Session, D3-pending):
  `web/app.py`, `web/templates/dashboard.html`, `web/dashboard_feed.py`, `web/routes/lfg_route.py`.
  → Router-Registrierung für neue RBAC-Routen: in eine **separate Datei** vorbereiten ODER als TODO im Handoff-Rückgabe-Report listen, damit die Haupt-Session sie in `web/app.py` einträgt (vermeidet app.py-Merge-Konflikt). `web/auth.py` darfst du anfassen (R1) — aber sauber, additiv (die Haupt-Session hat dort `get_ws_user` ergänzt, das bleibt).
- **Tests Pflicht:** alle 4 (`test_imports/routes/cogs/env_completeness`) + neue RBAC-Tests grün. `py_compile` + `bandit` clean. **Kein Deploy.**
- Datei-Reads NUR via Read-Tool (kein cat/Get-Content) — Security-Flag-Regel.

---

## Marco-Vorgaben (aus Fragenkatalog v2, B1–B5)

- **Discord-Rollen-getrieben:** Marco weist in Discord eine Rolle zu → User erhält automatisch den Dashboard-Zugriff. Kein separates Dashboard-Rollen-System.
- **Per-Bereich-Rollen:** z.B. „minecraft"-Rolle = nur MC-Bereich + Schreibrechte dort.
- **Member-Default:** alles **ansehen** (`view`), nichts editieren.
- **Audit-Log Pflicht:** wer hat was im Dashboard geändert (für Marco sichtbar).
- **Owner (Marco):** alles. Owner-ID bleibt ENV (`OWNER_ID`), NICHT über die Map regelbar (Selbst-Aussperr-Schutz).

---

## Scope = R1, R2, R3 (NICHT R4)

### R1 — Auth-Refactor + Permission-Mechanik
1. **Migration (additiv, idempotent)** in `modules/database/migrations.py` (nächste Version, aktuell ist v8 → **v9**; `CREATE TABLE IF NOT EXISTS`, schema_migrations-Tracking, FK-Indices):
   ```sql
   CREATE TABLE IF NOT EXISTS rbac_role_map (
     role_id TEXT NOT NULL, resource TEXT NOT NULL, action TEXT NOT NULL,
     PRIMARY KEY (role_id, resource, action));
   CREATE TABLE IF NOT EXISTS dashboard_audit (
     id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     discord_id TEXT, username TEXT, resource TEXT, action TEXT, detail TEXT, ip TEXT);
   CREATE INDEX IF NOT EXISTS idx_audit_ts ON dashboard_audit(ts);
   ```
   → eigener Migrations-Test `tests/test_migration_v9.py` (Muster: `tests/test_migration_v8.py`).
2. **`web/auth.py`:** `current_user`-Dict um `roles` (Discord-Rollen-IDs des Users) + `perms` erweitern. **ABER:** die Login-Identität liefert aktuell nur User-ID; Discord-Rollen des Users müssen beim Login geholt werden (OAuth-Scope `guilds.members.read` ODER Bot-seitig auflösen). **Klärungs-Punkt:** prüfe wie `web/auth.py` aktuell die Guild-Mitgliedschaft prüft (es gibt schon GUILD-Check beim Login ~Zeile 309–325) — Rollen evtl. dort schon verfügbar. Wenn nicht trivial: Rollen-Auflösung als TODO markieren, Framework mit leeren `roles` bauen.
3. **`require_perm(resource, action)`-Dependency-Factory** (analog `require_auth`): Owner → immer erlaubt; sonst Vereinigung der Grants aus `rbac_role_map` für die Rollen des Users; Member-Default = `view` auf alles. Bei fehlendem Recht → 403 (HTML: 303→/auth/login wenn unauth, 403 wenn auth-aber-kein-Recht). **Server-seitig autoritativ.**
4. **Resources/Actions** als Konstanten: Resources `{minecraft, satisfactory, leveling, moderation, lfg, temp_voice, admin_bot, system, rbac, audit}`, Actions `{view, edit, control}`. **(Bereichs-Schnitt MC=1-vs-getrennt ist offen → bau generisch, nutze vorerst `minecraft` als 1 Bereich, TODO-Marker.)**
5. Tests: `tests/test_rbac.py` — owner=all, member=view-only/no-edit, role-grant greift, kein-Recht=403, per-Guild/Isolation.

### R2 — Audit-Log
1. `modules/dashboard_audit.py` (neu): `async def log_action(discord_id, username, resource, action, detail: dict|None, ip)` → INSERT via `DBHelper.execute` (Write-Retry). Best-Effort, nie fatal. **Keine Secrets in `detail`.**
2. Audit-Write an **bestehende** edit/control-Aktionen hängen (Config-Saves, Restart, künftig Ban). Pragmatisch: zentral in `require_perm` für `edit`/`control` ODER an den jeweiligen POST-Routen. (Wenn zu invasiv über viele Routen → erstmal nur an die neuen RBAC-Routen + dokumentieren welche noch fehlen.)
3. **`/audit`-Seite** (Owner-only via `require_perm('audit','view')`): Tabelle + Filter (User/Bereich/Zeit). Route-Datei `web/routes/audit_route.py` + Template `audit.html` + Nav-Eintrag (Block in `base_v5.html` — **aber base_v5.html nicht in dieser Session deployen**, nur Branch). Muster: `web/routes/moderation_route.py` + `moderation.html`.
4. Test `tests/test_audit_web.py`.

### R3 — Rollen-Mapping-UI (`/rbac`, Owner-only)
1. `web/routes/rbac_route.py`: GET `/rbac` (Liste Rolle→Bereich+Aktionen aus `rbac_role_map`), POST `/rbac/config` (Mapping setzen/entfernen). HTMX + globale CSRFMiddleware (kein eigener CSRF-Dep — Middleware ist global) + `require_perm('rbac','edit')`. Snowflake-Validierung der Rollen-IDs.
2. Discord-Rollen-Dropdown: Rollen der Haupt-Guild — wie kommt das Web an die Rollen-Liste? (member_cache-Muster gibt's für Namen; Rollen evtl. nicht gecacht.) **Falls nicht verfügbar: Freitext-Rollen-ID-Eingabe** (wie LFG `lfg.role_id`) statt Dropdown — einfacher + reicht.
3. Template `rbac.html` + Partial `partials/rbac_config.html` (Muster: `lfg.html` + `partials/lfg_config.html`).
4. Test `tests/test_rbac_web.py`.

---

## NICHT bauen (Marco-Gates, R4 + offen)

- **R4 Edit-Matrix:** welche Rolle darf `edit` vs `control` je Bereich (z.B. MC-Rolle: Server-Restart ja/nein?) — **leer lassen**, Marco füllt via `/rbac`-UI später.
- **Bereichs-Schnitt:** MC = 1 Bereich oder BMC5/Vanilla getrennt — generisch bauen, Default 1 Bereich, TODO.
- **Exakte Rollen-Namen** — keine hardcoden.
- **UI-Hide (R5)** + 2.-Guild-Isolations-Test — nicht in diesem Handoff (später).

---

## Rückgabe an die Haupt-Session (Report am Ende)

Liste im Abschluss-Report:
1. Branch-Name + Commits.
2. **Welche Zeilen in `web/app.py` ergänzt werden müssen** (Router-Imports + `include_router` für audit_router + rbac_router) — die Haupt-Session trägt sie ein (app.py-Konflikt-Schutz).
3. **Welche Nav-Blöcke in `base_v5.html`** nötig sind (`nav_rbac`, `nav_audit`).
4. Offene Klärungen (Rollen-Auflösung beim Login? Bereichs-Schnitt? Audit-Coverage-Lücken).
5. Test-Status (4/4 + neue) + bandit.

> **Sicherheits-Hinweis:** RBAC fasst Login-/Auth-Code an (production-kritisch). Sorgfältig, additiv, nichts am bestehenden Login-Flow brechen (Marco = Owner muss weiter alles können). `/security-audit` oder `/review` auf den Branch vor Merge.
