"""
Datenschutzerklärung und Impressum (DSGVO/DDG, 21.10.2026).

  /datenschutz, /impressum   öffentlich (allow_anon), auf jeder Seite verlinkt
  /rechtliches               nur Owner: Kontaktangaben pflegen

Die Kontaktangaben stehen in `guild_config` (Keys `rechtliches.*`), nicht im
Code: Das Repo ist auf GitHub, Name und Anschrift des Betreibers gehören nicht
hinein. Grundlage der Texte: docs/recherche/DSGVO_DASHBOARD_2026-10-21.md.
Die Löschfristen im Text entsprechen modules/database/maintenance.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from modules.database.maintenance import (
    DEFAULT_RETENTION,
    MOD_NOTES_DAYS,
    SESSION_IP_DAYS,
    WARN_INACTIVE_DAYS,
)
from modules.guild_context import GuildConfig, get_primary_guild_id
from utils.logger import get_logger
from web.auth import allow_anon, require_auth
from web.templates_setup import erstelle_templates

logger = get_logger("web.routes.rechtliches")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = erstelle_templates(TEMPLATE_DIR)

router = APIRouter(tags=["Rechtliches"])

STAND = "21.10.2026"
FELDER = {
    "name": ("Name oder Bezeichnung des Betreibers", 120),
    "email": ("E-Mail für Datenschutz-Anfragen", 120),
    "discord": ("Discord-Name für Anfragen (optional)", 60),
    "anschrift": ("Anschrift (nur falls ein Impressum nötig ist, optional)", 300),
    "bundesland": ("Bundesland (für die zuständige Aufsichtsbehörde)", 40),
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def lade_kontakt() -> dict:
    """Kontaktangaben aus guild_config (leere Strings, wenn nicht gepflegt)."""
    gid = get_primary_guild_id()
    werte = {k: "" for k in FELDER}
    if gid is None:
        return werte
    for k in FELDER:
        try:
            werte[k] = str(await GuildConfig.get(gid, f"rechtliches.{k}", "") or "")
        except Exception as e:  # noqa: BLE001 — Seite muss auch ohne DB erscheinen
            logger.error(f"Rechtliches: {k} nicht lesbar: {e}")
    return werte


def fristen() -> dict:
    """Löschfristen in Tagen, direkt aus der Wartungs-Konfiguration."""
    tage = {tab: days for tab, (_col, days) in DEFAULT_RETENTION.items()}
    tage.update({
        "session_ip": SESSION_IP_DAYS,
        "warns": WARN_INACTIVE_DAYS,
        "mod_notes": MOD_NOTES_DAYS,
    })
    return tage


def _ist_owner(user: dict) -> bool:
    return user.get("is_owner") in (True, "True", "true")


@router.get("/datenschutz", response_class=HTMLResponse, dependencies=[Depends(allow_anon)])
async def datenschutz(request: Request):
    return templates.TemplateResponse(request, "rechtliches_seite.html", {
        "request": request, "seite": "datenschutz", "k": await lade_kontakt(),
        "fristen": fristen(), "stand": STAND,
    })


@router.get("/impressum", response_class=HTMLResponse, dependencies=[Depends(allow_anon)])
async def impressum(request: Request):
    return templates.TemplateResponse(request, "rechtliches_seite.html", {
        "request": request, "seite": "impressum", "k": await lade_kontakt(),
        "fristen": fristen(), "stand": STAND,
    })


@router.get("/rechtliches", response_class=HTMLResponse)
async def rechtliches_form(request: Request, current_user: dict = Depends(require_auth)):
    if not _ist_owner(current_user):
        return RedirectResponse("/home", status_code=303)
    return templates.TemplateResponse(request, "rechtliches_form.html", {
        "request": request, "user": current_user, "k": await lade_kontakt(),
        "felder": FELDER, "fehler": "", "ok": request.query_params.get("ok") == "1",
    })


@router.post("/rechtliches", response_class=HTMLResponse)
async def rechtliches_speichern(request: Request, current_user: dict = Depends(require_auth)):
    if not _ist_owner(current_user):
        return RedirectResponse("/home", status_code=303)
    form = await request.form()
    werte = {k: " ".join(str(form.get(k, "") or "").split())[:laenge] for k, (_t, laenge) in FELDER.items()}
    # Anschrift darf Zeilen haben
    werte["anschrift"] = "\n".join(
        z.strip() for z in str(form.get("anschrift", "") or "").splitlines() if z.strip()
    )[:FELDER["anschrift"][1]]
    fehler = ""
    if werte["email"] and not _EMAIL.match(werte["email"]):
        fehler = "Die E-Mail-Adresse sieht nicht gültig aus."
    elif not werte["email"] and not werte["discord"]:
        fehler = "Mindestens ein Kontaktweg (E-Mail oder Discord) ist Pflicht."
    gid = get_primary_guild_id()
    if not fehler and gid is not None:
        for k, v in werte.items():
            await GuildConfig.set(gid, f"rechtliches.{k}", v, updated_by=current_user.get("username", "dashboard"))
        return RedirectResponse("/rechtliches?ok=1", status_code=303)
    return templates.TemplateResponse(request, "rechtliches_form.html", {
        "request": request, "user": current_user, "k": werte, "felder": FELDER,
        "fehler": fehler or "Keine Haupt-Guild gesetzt.", "ok": False,
    })
