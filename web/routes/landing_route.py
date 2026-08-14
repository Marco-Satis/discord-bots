"""
D9: Öffentliche Landing-Page (Route `/`) + Live-Stat-Partial.

Anonym erreichbar (`allow_anon`). Zeigt Live-Status (Server/System/Bots) aus
denselben Sammlern wie das Dashboard — ABER **ohne** das Event-Log
(`collect_recent_events`), Privacy-Schnitt: die öffentliche Seite liest die
`events`-Tabelle gar nicht erst (kein Leak von Admin-Aktionen/Spieler-Namen).

Der Stat-Strip auf der Landing pollt alle 30s `GET /partials/landing-stats`
(rendert `_landing_stats.html`).
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.auth import allow_anon
from web.dashboard_feed import (
    collect_server_status,
    collect_system_stats,
    collect_bot_status,
)

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()


async def _public_payload() -> dict:
    """Live-Daten für öffentliche Seiten — OHNE Event-Log (Privacy-Schnitt)."""
    servers, system, bots = await asyncio.gather(
        asyncio.to_thread(collect_server_status),
        asyncio.to_thread(collect_system_stats),
        asyncio.to_thread(collect_bot_status),
    )
    return {"servers": servers, "system": system, "bots": bots}


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(allow_anon)])
async def landing(request: Request):
    """Öffentliche Landing-Page mit Live-Status (ohne Event-Log)."""
    data = await _public_payload()
    return templates.TemplateResponse(request, "landing.html", {"request": request, **data})


@router.get("/partials/landing-stats", response_class=HTMLResponse,
            dependencies=[Depends(allow_anon)])
async def landing_stats(request: Request):
    """30s-Poll-Target: rendert nur den Live-Stat-Strip."""
    data = await _public_payload()
    return templates.TemplateResponse(request, "_landing_stats.html", {"request": request, **data})
