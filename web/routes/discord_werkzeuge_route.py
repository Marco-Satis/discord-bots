"""
Drei Seiten für den Discord-Server (21.10.2026):

  /sicherheit-uebersicht  Ampel aller Schutzfunktionen (web/sicherheit_uebersicht.py)
  /booster                Danke-Nachricht beim Boost (cogs/supporter_cog.py liest die Keys)
  /geplant                Geplante Nachrichten (gemeinsame Ablage mit Recon /schedule)

Alle hinter require_perm("marshal_bot", "edit"): Mitglieder haben „view“ per Default,
und die Seiten zeigen Schwellen, Rechte und unveröffentlichte Texte.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from modules import geplante_nachrichten
from modules.channel_snapshot import read_snapshot
from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger
from web import sicherheit_uebersicht
from web.auth import require_perm
from web.module_uebersicht import lade_quellen
from web.templates_setup import erstelle_templates

logger = get_logger("web.routes.discord_werkzeuge")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Discord-Werkzeuge"])
_PERM = require_perm("marshal_bot", "edit")

DANKE_STANDARD = "💎 Danke {user} für deinen Boost! Du bist jetzt HQ-Supporter."  # wie cogs/supporter_cog.py
WIEDERHOLUNG_TEXT = {"einmalig": "einmalig", "taeglich": "täglich", "woechentlich": "wöchentlich"}
MELDUNG = {
    "angelegt": ("ok", "Nachricht geplant."),
    "geloescht": ("ok", "Geplante Nachricht gelöscht."),
    "fehlt": ("bad", "Diese Nachricht gibt es nicht mehr."),
}


_BEKANNTE_FEHLER = {
    "Bitte einen Kanal wählen.", "Bitte Datum und Uhrzeit angeben.", "Die Nachricht ist leer.",
    f"Die Nachricht darf höchstens {geplante_nachrichten.MAX_LAENGE} Zeichen haben.",
    "Unbekannte Wiederholung.", "Der Zeitpunkt liegt in der Vergangenheit.",
    f"Es sind schon {geplante_nachrichten.MAX_AKTIV} Nachrichten geplant (Höchstzahl).",
}


def _kanalnamen() -> dict[str, str]:
    return {str(c.get("id")): c.get("name", "") for c in read_snapshot().get("channels", [])}


# ---------------------------------------------------------------- Sicherheit

@router.get("/sicherheit-uebersicht", response_class=HTMLResponse)
async def sicherheit_seite(request: Request, current_user: dict = Depends(_PERM)):
    q = await lade_quellen()
    snap = await asyncio.to_thread(read_snapshot)
    marshal = next((r for r in snap.get("roles", [])
                    if r.get("managed") and "marshal" in str(r.get("name", "")).lower()), None)
    punkte = sicherheit_uebersicht.pruefungen(
        q, snap.get("sicherheit") or {}, marshal.get("bot_rechte") if marshal and "bot_rechte" in marshal else None,
    )
    return templates.TemplateResponse(request, "sicherheit_uebersicht.html", {
        "request": request, "user": current_user, "punkte": punkte,
        "summe": sicherheit_uebersicht.zusammenfassung(punkte), "stand": snap.get("updated_at"),
    })


# ---------------------------------------------------------------- Booster

async def _booster_kontext(gid) -> dict:
    return {
        "aktiv": bool(await GuildConfig.get(gid, "booster.danke_aktiv", False)),
        "kanal": str(await GuildConfig.get(gid, "booster.danke_kanal_id", "") or ""),
        "text": await GuildConfig.get(gid, "booster.danke_text", DANKE_STANDARD) or DANKE_STANDARD,
    }


@router.get("/booster", response_class=HTMLResponse)
async def booster_seite(request: Request, current_user: dict = Depends(_PERM)):
    gid = get_primary_guild_id()
    return templates.TemplateResponse(request, "booster.html", {
        "request": request, "user": current_user, "cfg": await _booster_kontext(gid),
        "success": "", "error": "",
    })


@router.post("/booster", response_class=HTMLResponse)
async def booster_speichern(request: Request, current_user: dict = Depends(_PERM)):
    gid = get_primary_guild_id()
    success = error = ""
    try:
        form = await request.form()
        aktiv = "aktiv" in form
        kanal = str(form.get("kanal", "") or "").strip()
        text = str(form.get("text", "") or "").strip()
        if kanal and not kanal.isdigit():
            raise ValueError("Ungültiger Kanal.")
        if aktiv and not kanal:
            raise ValueError("Für die Danke-Nachricht fehlt ein Kanal.")
        if len(text) > 1500:
            raise ValueError("Der Text darf höchstens 1500 Zeichen haben.")
        user = current_user.get("username", "dashboard")
        await GuildConfig.set(gid, "booster.danke_aktiv", aktiv, updated_by=user)
        await GuildConfig.set(gid, "booster.danke_kanal_id", kanal, updated_by=user)
        await GuildConfig.set(gid, "booster.danke_text", text or DANKE_STANDARD, updated_by=user)
        success = "Gespeichert." + (" Beim nächsten Boost bedankt sich Marshal." if aktiv else " Die Danke-Nachricht ist aus.")
    except ValueError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Booster speichern fehlgeschlagen: {e}")
        error = "Speichern fehlgeschlagen. Details im Server-Log."
    return templates.TemplateResponse(request, "partials/booster_form.html", {
        "request": request, "user": current_user, "cfg": await _booster_kontext(gid),
        "success": success, "error": error,
    })


# ---------------------------------------------------------------- Geplant

def _geplant_zeilen() -> list[dict]:
    namen = _kanalnamen()
    zeilen = []
    for m in geplante_nachrichten.liste():
        try:
            termin = datetime.fromisoformat(m["next_run"]).astimezone(geplante_nachrichten.BERLIN)
            wann = termin.strftime("%d.%m.%Y %H:%M")
        except (KeyError, ValueError):
            wann = "?"
        zeilen.append({
            "id": m.get("id"),
            "wann": wann,
            "kanal": namen.get(str(m.get("channel_id"))) or m.get("channel_name") or "unbekannter Kanal",
            "wiederholung": WIEDERHOLUNG_TEXT.get(m.get("repeat", "einmalig"), m.get("repeat")),
            "text": m.get("message", ""),
            "von": m.get("created_by", ""),
        })
    return zeilen


@router.get("/geplant", response_class=HTMLResponse)
async def geplant_seite(request: Request, current_user: dict = Depends(_PERM)):
    code = request.query_params.get("m", "")
    art, text = MELDUNG.get(code, ("", ""))
    # Nur bekannte Meldungen anzeigen: der Parameter steht in der URL und ist frei formbar
    fehler = request.query_params.get("fehler", "")
    if fehler not in _BEKANNTE_FEHLER:
        fehler = ""
    return templates.TemplateResponse(request, "geplant.html", {
        "request": request, "user": current_user,
        "zeilen": await asyncio.to_thread(_geplant_zeilen),
        "meldung_ok": text if art == "ok" else "",
        "meldung_fehler": text if art == "bad" else "",
        "formfehler": fehler,
        "max": geplante_nachrichten.MAX_AKTIV,
    })


@router.post("/geplant")
async def geplant_anlegen(request: Request, current_user: dict = Depends(_PERM)):
    form = await request.form()
    try:
        kanal = str(form.get("kanal", "") or "").strip()
        if not kanal.isdigit():
            raise ValueError("Bitte einen Kanal wählen.")
        roh = str(form.get("zeitpunkt", "") or "").strip()
        try:
            zeitpunkt = datetime.strptime(roh, "%Y-%m-%dT%H:%M").replace(tzinfo=geplante_nachrichten.BERLIN)
        except ValueError:
            raise ValueError("Bitte Datum und Uhrzeit angeben.") from None
        await asyncio.to_thread(
            geplante_nachrichten.hinzufuegen, str(form.get("text", "")), int(kanal),
            _kanalnamen().get(kanal, ""), zeitpunkt, str(form.get("wiederholung", "einmalig")),
            f"{current_user.get('username', 'dashboard')} (Dashboard)",
        )
    except ValueError as e:
        from urllib.parse import quote
        return RedirectResponse(f"/geplant?fehler={quote(str(e))}", status_code=303)
    return RedirectResponse("/geplant?m=angelegt", status_code=303)


@router.post("/geplant/{eintrag_id}/loeschen")
async def geplant_loeschen(eintrag_id: int, current_user: dict = Depends(_PERM)):
    ok = await asyncio.to_thread(geplante_nachrichten.loeschen, eintrag_id)
    return RedirectResponse(f"/geplant?m={'geloescht' if ok else 'fehlt'}", status_code=303)
