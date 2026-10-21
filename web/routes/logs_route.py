"""
Log-Kanäle im Dashboard (Vorbild Sapphire/Carl, 21.10.2026).

Bisher ließen sich die Log-Kanäle nur per `/audit setup` in Discord setzen.
Die Seite schreibt dieselbe Datei (data/admin/audit_config.json) über
modules.audit_logger.AuditLogger; Marshal liest sie bei der nächsten Meldung neu.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from modules.audit_logger import CATEGORY_TO_KEY, AuditLogger
from utils.logger import get_logger
from web.auth import require_perm
from web.templates_setup import erstelle_templates

logger = get_logger("web.routes.logs")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Log-Kanäle"])

# Kategorie -> (Titel, was dort landet). Reihenfolge = Anzeige.
BEREICHE = {
    "mod": ("Moderation", "Bann, Kick, Timeout, Verwarnungen"),
    "join": ("Beitritte", "Wer kommt und geht, neue Konten markiert"),
    "member": ("Mitglieder", "Nicknames, Avatare, Rollen einzelner Mitglieder"),
    "role": ("Rollen", "Rollen angelegt oder gelöscht"),
    "server": ("Server", "Kanäle, Webhooks, Threads, Einladungen"),
    "message": ("Nachrichten", "Gelöschte und bearbeitete Nachrichten"),
    "voice": ("Voice", "Beitritt, Wechsel, Verlassen von Sprachkanälen"),
}


def _zeilen() -> list[dict]:
    cfg = AuditLogger().get_config()
    return [
        {"kategorie": kat, "titel": titel, "text": text,
         "wert": str(cfg.get(CATEGORY_TO_KEY[kat]) or "")}
        for kat, (titel, text) in BEREICHE.items()
    ]


def _partial(request: Request, user: dict, success: str = "", error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/logs_config.html", {
        "request": request, "user": user, "zeilen": _zeilen(),
        "success": success, "error": error,
    })


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, current_user: dict = Depends(require_perm("marshal_bot", "edit"))):
    """Seite „Log-Kanäle“."""
    return templates.TemplateResponse(request, "logs.html", {
        "request": request, "user": current_user, "zeilen": _zeilen(),
        "success": "", "error": "",
    })


@router.post("/logs", response_class=HTMLResponse)
async def logs_save(request: Request, current_user: dict = Depends(require_perm("marshal_bot", "edit"))):
    """Log-Kanäle speichern. Leere Auswahl = Bereich aus."""
    try:
        form = await request.form()
        audit = AuditLogger()
        for kat in BEREICHE:
            roh = str(form.get(f"kanal_{kat}", "") or "").strip()
            if roh and not roh.isdigit():
                raise ValueError(f"Ungültiger Kanal für {kat}")
            audit.set_channel(kat, int(roh) if roh else 0)
        logger.info(f"Log-Kanäle gespeichert von {current_user.get('username', '?')}")
        return _partial(request, current_user, success="Gespeichert. Marshal nutzt die Kanäle ab der nächsten Meldung.")
    except Exception as e:
        logger.error(f"Log-Kanäle speichern fehlgeschlagen: {e}")
        return _partial(request, current_user, error="Speichern fehlgeschlagen. Details im Server-Log.")
