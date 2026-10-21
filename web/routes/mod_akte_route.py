"""
Moderations-Akte im Dashboard.

  GET  /moderation/akte                                   Uebersicht (Suche: ?q=)
  GET  /moderation/akte/{user_id}                         Zeitleiste eines Mitglieds
  GET  /moderation/akte/beweis/{datei}                    Beweisbild ausliefern
  POST /moderation/akte/{user_id}/notiz                   Notiz anlegen (nur Text)
  POST /moderation/akte/{user_id}/notiz/{note_id}/loeschen  Notiz loeschen
  POST /moderation/akte/{user_id}/warn/{warn_id}/grund    Grund einer Verwarnung aendern

Rechte: alles verlangt `moderation:edit`. Bewusst NICHT `moderation:view` —
das ist Member-Default (modules.rbac.MEMBER_VIEW_RESOURCES), und eine Akte mit
Verwarnungsgruenden, Notizen und Beweisbildern darf nicht jedes eingeloggte
Mitglied sehen.

CSRF: globale CSRFMiddleware; die Formulare senden `csrf_token` als Feld
(urlencoded, wird von der Middleware gelesen). Schreibende Routen antworten mit
303 auf die Detailseite (Post/Redirect/Get), die Meldung kommt als fester Code
(`?ok=`/`?err=`) — kein Nutzertext wird reflektiert.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from modules import mod_akte
from modules.dashboard_audit import log_from_user
from modules.guild_context import get_primary_guild_id
from utils.client_ip import client_ip_from_scope
from utils.logger import get_logger
from web.auth import require_perm
from web.templates_setup import erstelle_templates

logger = get_logger("web.routes.mod_akte")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Moderation"])

_PERM = require_perm("moderation", "edit")

# Feste Meldungen (Code -> Text). Unbekannte Codes werden ignoriert.
OK_MESSAGES: dict[str, str] = {
    "notiz": "Notiz gespeichert.",
    "geloescht": "Notiz gelöscht.",
    "grund": "Grund der Verwarnung geändert. Im Bot zeigt /warn list ihn nach dem nächsten Neustart.",
}
ERR_MESSAGES: dict[str, str] = {
    "leer": "Der Text darf nicht leer sein.",
    "lang": "Der Text ist zu lang.",
    "fehlt": "Diesen Eintrag gibt es nicht (mehr).",
    "fehler": "Speichern fehlgeschlagen. Details im Server-Log.",
    "server": "Keine Haupt-Guild gesetzt (ENV GUILD_ID).",
}


def _valid_user_id(user_id: str) -> str:
    """Pfad-Parameter als Discord-ID pruefen (sonst 404)."""
    if not user_id.isdigit() or len(user_id) > 25:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return user_id


def _back(user_id: str, ok: str = "", err: str = "") -> RedirectResponse:
    """303 zurueck auf die Detailseite mit Meldungs-Code."""
    query = f"?ok={ok}" if ok else (f"?err={err}" if err else "")
    return RedirectResponse(url=f"/moderation/akte/{user_id}{query}", status_code=303)


def _err_code(exc: ValueError) -> str:
    """ValueError aus mod_akte auf einen Meldungs-Code abbilden."""
    return "lang" if "länger" in str(exc) else "leer"


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

@router.get("/moderation/akte", response_class=HTMLResponse)
async def akte_list_page(request: Request, q: str = "", current_user: dict = Depends(_PERM)):
    """Uebersicht: alle Mitglieder mit Eintraegen, neueste Aktivitaet zuerst."""
    guild_id = get_primary_guild_id()
    q = (q or "").strip()[:80]
    akten: list[dict] = []
    error = ""
    if guild_id is not None:
        try:
            akten = await mod_akte.list_akten(guild_id, q)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Akten-Uebersicht laden fehlgeschlagen: {e}", exc_info=True)
            error = "Die Akten konnten nicht geladen werden. Details im Server-Log."
    totals = {
        "mitglieder": len(akten),
        "warns_active": sum(a["warns_active"] for a in akten),
        "actions": sum(a["actions"] for a in akten),
        "notes": sum(a["notes"] for a in akten),
    }
    return templates.TemplateResponse(request, "mod_akte.html", {
        "request": request,
        "user": current_user,
        "guild_set": guild_id is not None,
        "akten": akten,
        "totals": totals,
        "q": q,
        "error": error,
    })


@router.get("/moderation/akte/beweis/{datei}")
async def akte_evidence(datei: str, current_user: dict = Depends(_PERM)):
    """Beweisbild ausliefern — nur Namen aus der Whitelist, nur aus dem Evidence-Ordner."""
    path = mod_akte.evidence_path(datei)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    ext = path.suffix.lower().lstrip(".")
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, f"image/{ext}")
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'",
        },
    )


@router.get("/moderation/akte/{user_id}", response_class=HTMLResponse)
async def akte_detail_page(
    request: Request, user_id: str, ok: str = "", err: str = "",
    current_user: dict = Depends(_PERM),
):
    """Zeitleiste eines Mitglieds: Verwarnungen, Moderations-Taten, Notizen."""
    uid = _valid_user_id(user_id)
    guild_id = get_primary_guild_id()
    data = None
    error = ERR_MESSAGES.get(err, "")
    if guild_id is None:
        error = ERR_MESSAGES["server"]
    else:
        try:
            data = await mod_akte.akte(guild_id, uid)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Akte laden fehlgeschlagen: {e}", exc_info=True)
            error = "Die Akte konnte nicht geladen werden. Details im Server-Log."
    return templates.TemplateResponse(request, "mod_akte_detail.html", {
        "request": request,
        "user": current_user,
        "guild_set": guild_id is not None,
        "user_id": uid,
        "akte": data,
        "success": OK_MESSAGES.get(ok, ""),
        "error": error,
        "max_note": mod_akte.MAX_NOTE_LEN,
        "max_reason": mod_akte.MAX_REASON_LEN,
    })


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

@router.post("/moderation/akte/{user_id}/notiz")
async def akte_add_note(request: Request, user_id: str, current_user: dict = Depends(_PERM)):
    """Notiz (nur Text) zur Akte hinzufuegen."""
    uid = _valid_user_id(user_id)
    guild_id = get_primary_guild_id()
    if guild_id is None:
        return _back(uid, err="server")
    form = await request.form()
    text = str(form.get("text", "") or "")
    author = str(current_user.get("sub", "") or "")
    try:
        note_id = await mod_akte.add_note(guild_id, uid, author, text)
    except ValueError as e:
        return _back(uid, err=_err_code(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Akte-Notiz speichern fehlgeschlagen: {e}", exc_info=True)
        return _back(uid, err="fehler")
    if author.isdigit():
        await mod_akte.ensure_member_name(guild_id, author, current_user.get("username"))
    await log_from_user(
        current_user, "moderation", "edit",
        detail={"op": "akte_notiz_add", "user_id": uid, "note_id": note_id, "laenge": len(text.strip())},
        ip=client_ip_from_scope(request.scope),
    )
    return _back(uid, ok="notiz")


@router.post("/moderation/akte/{user_id}/notiz/{note_id}/loeschen")
async def akte_delete_note(
    request: Request, user_id: str, note_id: int, current_user: dict = Depends(_PERM)
):
    """Notiz (inkl. Beweisbild) loeschen."""
    uid = _valid_user_id(user_id)
    guild_id = get_primary_guild_id()
    if guild_id is None:
        return _back(uid, err="server")
    try:
        existing = await mod_akte.get_note(note_id)
        if existing is None or existing["user_id"] != uid:
            return _back(uid, err="fehlt")
        note = await mod_akte.delete_note(note_id, guild_id=guild_id)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Akte-Notiz loeschen fehlgeschlagen: {e}", exc_info=True)
        return _back(uid, err="fehler")
    if note is None:
        return _back(uid, err="fehlt")
    await log_from_user(
        current_user, "moderation", "edit",
        detail={"op": "akte_notiz_delete", "user_id": uid, "note_id": note_id,
                "beweisbild": bool(note.get("evidence_file"))},
        ip=client_ip_from_scope(request.scope),
    )
    return _back(uid, ok="geloescht")


@router.post("/moderation/akte/{user_id}/warn/{warn_id}/grund")
async def akte_warn_reason(
    request: Request, user_id: str, warn_id: int, current_user: dict = Depends(_PERM)
):
    """Grund einer Verwarnung aendern (nur Warns dieses Mitglieds)."""
    uid = _valid_user_id(user_id)
    form = await request.form()
    reason = str(form.get("grund", "") or "")
    try:
        warns = await mod_akte.list_warns(uid)
        if not any(w["id"] == warn_id for w in warns):
            return _back(uid, err="fehlt")
        res = await mod_akte.update_warn_reason(warn_id, reason)
    except ValueError as e:
        return _back(uid, err=_err_code(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Warn-Grund aendern fehlgeschlagen: {e}", exc_info=True)
        return _back(uid, err="fehler")
    if res is None:
        return _back(uid, err="fehlt")
    await log_from_user(
        current_user, "moderation", "edit",
        detail={"op": "warn_reason_update", "user_id": uid, "warn_id": warn_id},
        ip=client_ip_from_scope(request.scope),
    )
    return _back(uid, ok="grund")
