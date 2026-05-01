"""
Config Reload API — Erzwingt einen Config-Reload per REST-Endpunkt.

Stellt POST /api/config/reload bereit (Auth erforderlich).
Laedt die config.json neu, fuehrt alle registrierten Callbacks aus
und gibt zurueck, welche Callbacks erfolgreich ausgefuehrt wurden.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger("web.routes.config_reload")

router = APIRouter(tags=["Config Reload"])


def _is_owner(user: dict) -> bool:
    """Prueft ob der Benutzer Owner-Rechte hat."""
    return user.get("is_owner") in (True, "True", "true")


@router.post("/api/config/reload")
async def force_config_reload(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Erzwingt einen sofortigen Config-Reload.

    Laedt config.json neu und fuehrt alle registrierten Callbacks aus.
    Nur fuer authentifizierte Owner zugaenglich.

    Returns:
        JSON mit Status, ausgefuehrten Callbacks und Zeitstempel.
    """
    user = current_user
    if not _is_owner(user):
        logger.warning(
            "Config-Reload verweigert fuer Benutzer '%s' — keine Owner-Rechte",
            user.get("username", "Unbekannt"),
        )
        raise HTTPException(403, "Nur der Owner darf einen Config-Reload ausloesen")

    # --- ConfigReloader-Instanz holen ---
    try:
        from modules.config_reloader import get_reloader
        reloader = get_reloader()
    except ImportError:
        logger.error("config_reloader-Modul nicht verfuegbar")
        return JSONResponse(
            content={"error": "Config-Reloader-Modul nicht verfuegbar"},
            status_code=500,
        )

    if reloader is None:
        logger.error("Kein ConfigReloader initialisiert — init_reloader() aufrufen")
        return JSONResponse(
            content={"error": "Config-Reloader nicht initialisiert"},
            status_code=503,
        )

    # --- Reload ausfuehren ---
    username = user.get("username", "Unbekannt")
    logger.info("Config-Reload angefordert von '%s'", username)

    try:
        executed_callbacks = await reloader.force_reload()
    except Exception as exc:
        logger.error("Config-Reload fehlgeschlagen: %s", exc)
        return JSONResponse(
            content={
                "error": "Config-Reload fehlgeschlagen",
                "detail": str(exc),
            },
            status_code=500,
        )

    # --- Erfolgs-Antwort ---
    total_callbacks = reloader.callback_count
    all_callback_names = reloader.callback_names

    # Fehlgeschlagene Callbacks ermitteln
    failed_callbacks = [
        name for name in all_callback_names
        if name not in executed_callbacks
    ]

    response_data = {
        "status": "ok" if not failed_callbacks else "partial",
        "message": "Config erfolgreich neu geladen",
        "triggered_by": username,
        "callbacks": {
            "total": total_callbacks,
            "executed": executed_callbacks,
            "failed": failed_callbacks,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "Config-Reload abgeschlossen: %d/%d Callbacks erfolgreich (von '%s')",
        len(executed_callbacks),
        total_callbacks,
        username,
    )

    return JSONResponse(content=response_data, status_code=200)


@router.get("/api/config/reload/status")
async def reload_status(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Gibt den Status des ConfigReloaders zurueck.

    Zeigt ob der Watcher aktiv ist, welche Callbacks registriert sind
    und wann die letzte Aenderung erkannt wurde.

    Returns:
        JSON mit Reloader-Status.
    """
    # --- ConfigReloader-Instanz holen ---
    try:
        from modules.config_reloader import get_reloader
        reloader = get_reloader()
    except ImportError:
        return JSONResponse(
            content={"error": "Config-Reloader-Modul nicht verfuegbar"},
            status_code=500,
        )

    if reloader is None:
        return JSONResponse(
            content={
                "initialized": False,
                "message": "Config-Reloader nicht initialisiert",
            },
            status_code=200,
        )

    response_data = {
        "initialized": True,
        "running": reloader.is_running,
        "config_path": str(reloader.config_path),
        "check_interval": reloader.check_interval,
        "callbacks": {
            "count": reloader.callback_count,
            "names": reloader.callback_names,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return JSONResponse(content=response_data, status_code=200)
