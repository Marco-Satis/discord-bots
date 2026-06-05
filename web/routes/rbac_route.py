"""
RBAC-Verwaltungsseite (R3) — Discord-Rolle → Dashboard-Bereich+Aktion mappen.

Owner-only (sensible Ressource `rbac`). Schreibt die Tabelle `rbac_role_map`
(Migration v9), die `modules.rbac.require_perm`/`has_perm` zur Laufzeit auswertet.
Sobald Marco eine Discord-Rolle einem User gibt, greift das Mapping automatisch
(B3/B5 — keine zusaetzliche Dashboard-Zuweisung noetig).

Rollen-Eingabe als Freitext-Snowflake (wie LFG `lfg.role_id`) statt Dropdown —
der Web-Prozess hat die Guild-Rollen-Liste nicht zuverlaessig gecacht. HTMX +
globale CSRFMiddleware (kein eigener CSRF-Dep). Jede Aenderung wird auditiert.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import modules.rbac as rbac
from modules.dashboard_audit import log_from_user
from utils.logger import get_logger
from web.auth import require_perm

logger = get_logger("web.routes.rbac")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["RBAC"])

# Anzeige-Reihenfolge der Bereiche/Aktionen in der Checkbox-Matrix
_RESOURCE_ORDER = [
    "minecraft", "satisfactory", "leveling", "moderation", "lfg",
    "temp_voice", "admin_bot", "system", "rbac", "audit",
]
_ACTION_ORDER = ["view", "edit", "control"]


def _ordered_resources() -> list[str]:
    """Bereiche in stabiler Reihenfolge (Whitelist-gefiltert)."""
    return [r for r in _RESOURCE_ORDER if r in rbac.RESOURCES]


async def _ctx() -> dict:
    """Gemeinsamer Template-Kontext (aktuelle Map + Matrix-Achsen)."""
    role_map = await rbac.get_role_map()
    # set -> sortierte Liste fuer stabile Template-Ausgabe
    roles_view = {
        rid: sorted(grants) for rid, grants in sorted(role_map.items())
    }
    return {
        "role_map": roles_view,
        "resources": _ordered_resources(),
        "actions": _ACTION_ORDER,
        "sensitive": sorted(rbac.SENSITIVE),
    }


@router.get("/rbac", response_class=HTMLResponse)
async def rbac_page(
    request: Request, current_user: dict = Depends(require_perm("rbac", "view"))
):
    """Rollen-Mapping-Seite anzeigen (Owner-only)."""
    ctx = await _ctx()
    return templates.TemplateResponse("rbac.html", {
        "request": request,
        "user": current_user,
        "success": "",
        "error": "",
        **ctx,
    })


@router.post("/rbac/config", response_class=HTMLResponse)
async def rbac_config_save(
    request: Request, current_user: dict = Depends(require_perm("rbac", "edit"))
):
    """
    Mapping einer Rolle setzen (`op=save`) oder entfernen (`op=delete`).

    Felder: `role_id` (Snowflake), `op`, `grant` (mehrfach, je `resource:action`).
    `save` ersetzt alle Grants der Rolle durch die angehakten; `delete` nimmt die
    Rolle komplett aus dem Mapping.
    """
    success = ""
    error = ""
    client_ip = request.client.host if request.client else "unknown"

    try:
        form = await request.form()
        role_id = str(form.get("role_id", "") or "").strip()
        op = str(form.get("op", "save") or "save").strip()

        if not role_id.isdigit():
            raise ValueError("Rollen-ID muss numerisch sein (Discord-Snowflake).")

        if op == "delete":
            await rbac.delete_role(role_id)
            await log_from_user(
                current_user, "rbac", "edit",
                detail={"op": "delete_role", "role_id": role_id}, ip=client_ip,
            )
            success = f"Rolle {role_id} aus dem Mapping entfernt."
        else:
            # Angehakte Grants parsen (Werte "resource:action")
            raw_grants = form.getlist("grant")
            parsed: list[tuple[str, str]] = []
            for item in raw_grants:
                part = str(item)
                if ":" not in part:
                    continue
                resource, action = part.split(":", 1)
                parsed.append((resource.strip(), action.strip()))

            await rbac.set_role_grants(role_id, parsed, updated_by="dashboard")
            await log_from_user(
                current_user, "rbac", "edit",
                detail={
                    "op": "set_grants",
                    "role_id": role_id,
                    "grants": [f"{r}:{a}" for r, a in rbac.validate_grants(parsed)],
                },
                ip=client_ip,
            )
            logger.info(
                f"RBAC-Mapping gespeichert von "
                f"{current_user.get('username', 'Unbekannt')} (Rolle {role_id})"
            )
            success = f"Mapping fuer Rolle {role_id} gespeichert."
    except ValueError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001
        logger.error(f"RBAC-Mapping speichern fehlgeschlagen: {e}")
        error = "Fehler beim Speichern. Details im Server-Log."

    ctx = await _ctx()
    return templates.TemplateResponse("partials/rbac_config.html", {
        "request": request,
        "success": success,
        "error": error,
        **ctx,
    })
