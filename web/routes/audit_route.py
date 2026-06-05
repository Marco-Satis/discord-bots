"""
Audit-Log-Seite (R2) — wer hat was im Dashboard geaendert (B4).

Owner-only (sensible Ressource `audit`). Liest `dashboard_audit` (Migration v9)
ueber `modules.dashboard_audit.fetch_audit` mit optionalen Filtern (User/Bereich/
Zeit). Reine Lese-Seite — Filter via GET-Form (kein State-Change, kein CSRF).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import modules.rbac as rbac
from modules.dashboard_audit import fetch_audit
from utils.logger import get_logger
from web.auth import require_perm

logger = get_logger("web.routes.audit")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Audit"])

_DEFAULT_LIMIT = 200


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    current_user: dict = Depends(require_perm("audit", "view")),
    user: str = "",
    resource: str = "",
    since: str = "",
    until: str = "",
):
    """
    Audit-Log anzeigen (Owner-only) mit Filtern.

    Query-Parameter: `user` (discord_id/Name Teilstring), `resource` (Bereich),
    `since`/`until` (ISO-Zeit-Praefix, z.B. 2026-06-01).
    """
    # Bereichs-Filter gegen Whitelist absichern (leer = alle)
    resource_q = resource if resource in rbac.RESOURCES else ""

    entries = await fetch_audit(
        user_q=user.strip(),
        resource_q=resource_q,
        since=since.strip(),
        until=until.strip(),
        limit=_DEFAULT_LIMIT,
    )

    return templates.TemplateResponse("audit.html", {
        "request": request,
        "user_ctx": current_user,
        "entries": entries,
        "resources": sorted(rbac.RESOURCES),
        "limit": _DEFAULT_LIMIT,
        # Filter-Werte zurueck ins Formular spiegeln
        "f_user": user,
        "f_resource": resource_q,
        "f_since": since,
        "f_until": until,
    })
