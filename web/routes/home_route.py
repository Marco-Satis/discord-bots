"""
Startseite nach dem Login (Route `/home`, require_auth) — Bereich „Übersicht“ im Design C3.

Reihenfolge: Braucht Aufmerksamkeit, Kennzahlen, Bots, Game-Server,
Schnellzugriff, letzte Ereignisse. Logik in web/startseite.py.
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from web.templates_setup import erstelle_templates

from utils.logger import get_logger
from web.auth import require_auth
from web.dashboard_feed import collect_recent_events
from web.routes.landing_route import _public_payload
from web import startseite

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)
logger = get_logger(__name__)

router = APIRouter()


async def _modulkarten() -> list[dict]:
    """Modulkarten von Marshal (für „prüfen“-Hinweise). Fehler -> leere Liste."""
    try:
        from web.module_uebersicht import baue_karten, lade_quellen
        return baue_karten(await lade_quellen())
    except Exception as e:  # noqa: BLE001 — Startseite darf nie an einer Quelle scheitern
        logger.warning(f"Modulkarten für Startseite nicht ladbar: {e}")
        return []


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, current_user: dict = Depends(require_auth)):
    """Startseite: was Aufmerksamkeit braucht, Status, Schnellzugriff, Ereignisse."""
    data, events, karten = await asyncio.gather(
        _public_payload(), collect_recent_events(limit=60), _modulkarten(),
    )
    servers, system, bots = data["servers"], data["system"], data["bots"]
    try:
        from modules.monitoring.manual_stop_state import get_stopped_servers
        gestoppt = set(get_stopped_servers())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"manual_stop_state nicht lesbar: {e}")
        gestoppt = set()
    aenderungen = []
    if current_user.get("is_owner") in (True, "True", "true"):
        try:
            from modules.dashboard_audit import fetch_audit
            aenderungen = startseite.aenderungen_kompakt(await fetch_audit(limit=5))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Änderungsprotokoll für Startseite nicht lesbar: {e}")
    return templates.TemplateResponse(request, "home.html", {
        "request": request,
        "user": current_user,
        **data,
        "datum": startseite.datum_text(),
        "punkte": startseite.aufmerksamkeit(servers, system, bots, karten, gestoppt),
        "gestoppt": gestoppt,
        "bots_kurz": startseite.bots_kompakt(bots),
        "ereignisse": startseite.ereignisse_kompakt(events),
        "aenderungen": aenderungen,
        "schnellzugriff": startseite.SCHNELLZUGRIFF,
        "online": startseite.ONLINE,
    })
