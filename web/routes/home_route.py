"""
D9: Welcome-Home — Post-Login-Startseite (Route `/home`, require_auth).

Begrüßung + Status-Snapshot + Quick-Links. Bewusst leichter Schnitt: nutzt
dieselben Live-Sammler wie Landing/Dashboard, aber OHNE Event-Log (Home ist
Übersicht, nicht das Audit-Log — das lebt unter /dashboard bzw. /audit).
"""
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from web.auth import require_auth
from web.routes.landing_route import _public_payload

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter()


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, current_user: dict = Depends(require_auth)):
    """Post-Login-Startseite mit Status-Snapshot + Quick-Links."""
    data = await _public_payload()
    return templates.TemplateResponse(
        request, "home.html", {"request": request, "user": current_user, **data}
    )
