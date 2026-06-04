# RBAC R1–R3 — Rückgabe-Report an die Haupt-Session

> **Branch `feature/rbac` (von `main`-HEAD). Kein Deploy, kein Merge — wartet auf Review.**
> Scope erfüllt: R1 (Auth-Refactor + Permission-Mechanik), R2 (Audit-Log), R3 (Rollen-Mapping-UI).
> **NICHT** gebaut: R4 (Edit-Matrix) + R5 (UI-Hide/2.-Guild-Test) — bewusst leer (Marco-Gates).

---

## 1. Branch + Commits

- **Branch:** `feature/rbac`
- **Commit(s):** siehe `git log feature/rbac ^main` (1 Commit: „RBAC R1-R3 …").
- **Geänderte/neue Dateien:**

| Typ | Datei | Inhalt |
|-----|-------|--------|
| neu | `modules/rbac.py` | Permission-Kern: RESOURCES/ACTIONS, `has_perm`, `resolve_perms`, Mapping-CRUD |
| neu | `modules/dashboard_audit.py` | `log_action`/`log_from_user`/`fetch_audit` + Secret-Redaktion |
| neu | `web/routes/rbac_route.py` | GET `/rbac`, POST `/rbac/config` (Owner-only) |
| neu | `web/routes/audit_route.py` | GET `/audit` (Owner-only, Filter) |
| neu | `web/templates/rbac.html` + `partials/rbac_config.html` | Mapping-UI (HTMX) |
| neu | `web/templates/audit.html` | Audit-Tabelle + Filter (GET-Form) |
| neu | `tests/test_migration_v9.py`, `test_rbac.py`, `test_audit_web.py`, `test_rbac_web.py` | Tests |
| mod | `modules/database/migrations.py` | **v9** (`rbac_role_map` + `dashboard_audit`), CURRENT_VERSION 8→9 |
| mod | `web/auth.py` | `roles` ins JWT (additiv) + `require_perm(resource, action)`-Factory |
| mod | `web/templates/base_v5.html` | Nav-Sektion „Verwaltung" (RBAC + Audit), owner-gated |

**NICHT angefasst** (gehören der Haupt-Session): `web/app.py`, `web/templates/dashboard.html`,
`web/dashboard_feed.py`, `web/routes/lfg_route.py`.

---

## 2. `web/app.py` — diese Zeilen muss die Haupt-Session ergänzen (app.py-Konflikt-Schutz)

**Imports** (zum Block der `from web.routes.* import router as *_router`-Zeilen, ~Z. 248, nach `lfg_router`):

```python
from web.routes.audit_route import router as audit_router        # noqa: E402
from web.routes.rbac_route import router as rbac_router           # noqa: E402
```

**Registrierung** (zum `app.include_router(...)`-Block, ~Z. 271, nach `app.include_router(lfg_router)`):

```python
app.include_router(audit_router)
app.include_router(rbac_router)
```

> Beide Router haben **keinen** Prefix; Pfade sind `/rbac`, `/rbac/config`, `/audit`.
> Auth liegt pro-Route via `Depends(require_perm(...))` — kein Router-weiter Dependency nötig.
> Die globale `CSRFMiddleware` greift für POST `/rbac/config` automatisch (kein eigener CSRF-Dep).

---

## 3. `web/templates/base_v5.html` — Nav-Blöcke

**Bereits auf dem Branch ergänzt** (base_v5.html stand nicht auf der „nicht anfassen"-Liste).
Eingefügt nach dem LFG-Eintrag, owner-gated:

```jinja
{% if user and user.is_owner %}
<div class="sb-sec">Verwaltung</div>
<a class="nav-i {% block nav_rbac %}{% endblock %}" href="/rbac"> … RBAC …</a>
<a class="nav-i {% block nav_audit %}{% endblock %}" href="/audit"> … Audit-Log …</a>
{% endif %}
```

→ Falls die Haupt-Session base_v5.html ebenfalls editiert hat: **Merge-Konflikt möglich** an
dieser Stelle (nur diese ~10 Zeilen). Die Blöcke `nav_rbac`/`nav_audit` müssen erhalten bleiben,
sonst verliert `{% block nav_rbac %}active{% endblock %}` in rbac.html/audit.html sein Ziel
(unkritisch — Seite rendert trotzdem, nur ohne Active-Highlight).

---

## 4. Offene Klärungen (für Marco / Haupt-Session)

| # | Punkt | Aktueller Stand im Code | Was offen ist |
|---|-------|-------------------------|---------------|
| K1 | **Rollen-Auflösung beim Login** | **GELÖST.** `web/auth.py` holte die Guild-Member-Rollen schon beim OAuth-Callback (Scope `guilds.members.read` vorhanden, ~Z.314). Ich speichere sie jetzt additiv als `roles` im JWT. Kein OAuth-Scope-Change nötig. | Nichts — funktioniert für OAuth-Login. **Fallback-Passwort-Login** hat `roles=[]` (ist aber `is_owner=True` → alles). |
| K2 | **Bereichs-Schnitt MC** | Generisch: **1 Bereich `minecraft`** (TODO-Marker in `modules/rbac.py`). | Marco: BMC5/Vanilla getrennt (`mc_bmc`/`mc_vanilla`) oder 1 Bereich? Bei Trennung: RESOURCES erweitern + ggf. Migration bestehender Grants. |
| K3 | **Audit-Coverage** | Audit-Writes hängen **nur an den neuen RBAC-Routen** (`/rbac/config`). | Die **bestehenden** edit/control-Routen (Config-Save, System-Restart, Moderation/LFG-Save, künftig Ban) schreiben noch KEIN Audit. Siehe Liste unten — diese gehören der Haupt-Session bzw. R2-Folgearbeit. |
| K4 | **Member-Sicht auf Sensibles** | **Entscheidung getroffen** (Spec §8.4-Empfehlung): `system`, `audit`, `rbac` sind **sensibel** → kein Member-Default-`view`, nur Owner oder explizit gegrantet. Rest = `view` für Member. | Marco-Bestätigung erwünscht. Falls Member auch System/Audit read-only sehen sollen: `SENSITIVE` in `modules/rbac.py` anpassen. |

### Audit-Coverage-Lücken (K3) — wo `log_from_user(...)` noch fehlt
Pragmatisch (Handoff R2.2: „wenn zu invasiv über viele Routen → erstmal neue Routen + dokumentieren"):
- `web/routes/config_route.py` (Config-Save) → `log_from_user(user, "system", "edit", detail={...})`
- `web/routes/system_route.py` (Service-Restart/Aktionen) → `… "system", "control"`
- `web/routes/moderation_route.py` (Auto-Mod-Toggles) → `… "moderation", "edit"`
- `web/routes/lfg_route.py` (LFG-Config) → `… "lfg", "edit"`
- `web/routes/admin_bot_route.py` (Modul-Toggles) → `… "admin_bot", "edit"`
- künftige Ban-/RCON-Aktionen → `… "<bereich>", "control"`

> Muster: `from modules.dashboard_audit import log_from_user` + im POST-Handler
> `await log_from_user(current_user, "<resource>", "<action>", detail={…ohne Secrets…}, ip=request.client.host)`.

---

## 5. Test-Status + Bandit

| Test | Ergebnis |
|------|----------|
| `tests/test_migration_v9.py` | ✅ 9 Checks (Tabellen, Spalten, Index, PK-Dedup, Idempotenz) |
| `tests/test_rbac.py` | ✅ 27 Checks (owner=all, member=view-only/no-edit, Rollen-Grant, Isolation, fail-closed, Replace/Delete) |
| `tests/test_audit_web.py` | ✅ 14 Checks (log/fetch/Filter, Secret-Redaktion, best-effort, Auth-Gate 303/403) |
| `tests/test_rbac_web.py` | ✅ 15 Checks (Auth-Gate 303/403, DB-Persistenz save/delete, invalid-role) |
| **4 Pflicht-Tests** (`test_imports/routes/cogs/env_completeness`) | ✅ alle grün — **keine Regression** |
| `py_compile` (alle neuen/geänderten Files) | ✅ clean |
| `bandit` (6 Files) | ✅ 0 Medium/High (3 B608-False-Positives via `# nosec` begründet — dyn. `IN (…)`-Platzhalter, Werte parametrisiert) |

### Wichtiger Test-Hinweis (lokal vs. Server)
Die **lokale** Starlette (1.2.1) nutzt die **neue** `TemplateResponse(request, name, …)`-Signatur;
das Projekt (und die Server-Starlette) nutzt durchgängig die **alte** `(name, context)`-Signatur.
Dadurch 500t der Render-Pfad **lokal** für *jede* bestehende Route (nicht nur RBAC) — `test_routes.py`
ist deshalb rein statisch. Die Web-Tests prüfen den Render-200-Pfad daher nur, wenn die installierte
Starlette die alte Signatur hat (Server); Auth-Gate (303/403) + DB-Persistenz werden **immer** scharf
geprüft. **Auf dem Server rendern alle RBAC-Seiten regulär.**

---

## Sicherheits-Hinweise (vor Merge beachten)

- **Server-seitig autoritativ:** `require_perm` raised 403 bei fehlendem Recht — UI-Hide (R5) ist nur Komfort.
- **Owner = ENV `OWNER_ID`** (JWT-Claim `is_owner`), **nicht** über die Map regelbar (Selbst-Aussperr-Schutz).
- **Login-Flow unverändert** für bestehende User: `roles` ist additiv; Autorisierungs-Logik in `web/auth.py`
  semantisch identisch (nur Member-Fetch passiert jetzt immer bei gesetzter `GUILD_ID`, um Rollen zu kennen).
- **Empfehlung:** `/security-audit` oder `/review` auf den Branch vor Merge (Auth-Code = prod-kritisch).
- **Migration v9 additiv + idempotent** (CREATE TABLE IF NOT EXISTS, PRAGMA user_version-getrackt).
