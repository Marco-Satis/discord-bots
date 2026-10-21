"""
Änderungsprotokoll für ALLE schreibenden Dashboard-Anfragen (21.10.2026).

Befund: Von 15 Routen-Dateien mit POST-Routen schrieben nur RBAC, Sicherheit und die
Modul-Schalter ins Änderungsprotokoll (`dashboard_audit`). Einstellungen, Leveling,
Spielersuche, Automod, Server-Steuerung und System-Aktionen hinterließen keine Spur,
die Seite /audit war deshalb leer. Statt jede Route einzeln nachzurüsten (und jede
künftige zu vergessen), protokolliert diese Middleware jede erfolgreiche schreibende
Anfrage eines angemeldeten Benutzers: wer, welcher Bereich, welcher Pfad, Status.

Bewusst NICHT protokolliert: Formularinhalte (können Passwörter, Webhooks oder
Spielernamen enthalten). Pfade, deren Route selbst schon genauer protokolliert,
werden übersprungen, damit kein Eintrag doppelt erscheint.

Pure ASGI (wie session_timeout.py): der Request-Body wird nicht angefasst.
"""
from __future__ import annotations

import re
from typing import Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from utils.logger import get_logger

logger = get_logger("web.aenderungs_protokoll")

SCHREIBEND = {"POST", "PUT", "PATCH", "DELETE"}

# Keine Änderung oder eigene, genauere Protokollierung in der Route
UEBERSPRINGEN = (
    "/auth/", "/api/theme", "/api/search", "/api/webhook", "/api/guild/",
    "/rbac", "/marshal-bot/module/",
)
# Nur lesend, obwohl POST
NUR_LESEND = re.compile(r"/mods/(search|check-updates)$")

_SERVER = re.compile(r"^/api/server/([^/]+)/")
_STEUERN = re.compile(r"/(action|rcon|reboot|service|packages|mods)(/|$)")


def bereich(pfad: str) -> Optional[tuple[str, str]]:
    """
    Pfad -> (resource, action) für dashboard_audit, oder None (nicht protokollieren).

    resource folgt modules.rbac.RESOURCES; action ist „control“ für Steueraktionen
    (Start/Stopp, RCON, Pakete, Mods), sonst „edit“.
    """
    if any(pfad.startswith(p) for p in UEBERSPRINGEN) or NUR_LESEND.search(pfad):
        return None
    aktion = "control" if _STEUERN.search(pfad) else "edit"
    treffer = _SERVER.match(pfad)
    if treffer:
        return ("minecraft" if treffer.group(1).startswith("mc_") else "satisfactory"), aktion
    for praefix, resource in (
        ("/config/login", "rbac"),
        ("/leveling", "leveling"),
        ("/lfg", "lfg"),
        ("/moderation", "moderation"),
        ("/marshal-bot", "marshal_bot"),
        ("/tickets", "marshal_bot"),
    ):
        if pfad.startswith(praefix):
            return resource, aktion
    return "system", aktion


class AenderungsProtokollMiddleware:
    """Schreibt nach jeder erfolgreichen schreibenden Anfrage einen dashboard_audit-Eintrag."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in SCHREIBEND:
            await self.app(scope, receive, send)
            return
        pfad = scope.get("path", "")
        ziel = bereich(pfad)
        if ziel is None:
            await self.app(scope, receive, send)
            return

        status = {"code": 500}

        async def send_mit_status(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_mit_status)

        if status["code"] >= 400:
            return
        try:
            from starlette.requests import Request
            from web.auth import get_current_user
            from modules.dashboard_audit import log_from_user

            request = Request(scope)
            user = get_current_user(request)
            if not user:
                return
            ip = request.headers.get("x-real-ip") or (scope.get("client") or ("",))[0]
            await log_from_user(user, ziel[0], ziel[1], {"pfad": pfad, "status": status["code"]}, ip=ip)
        except Exception as e:  # noqa: BLE001 — Protokoll darf die Antwort nie kippen
            logger.warning(f"Änderungsprotokoll fehlgeschlagen ({pfad}): {e}")
