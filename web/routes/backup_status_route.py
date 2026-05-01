"""
F36: Backup Status Route — Cloud-Backup-Status als JSON-API.

Stellt GET /api/backup/cloud-status bereit, der den aktuellen
OneDrive-Backup-Status inkl. letztem Upload, Speicherverbrauch
und Upload-Historie zurueckgibt. Auth erforderlich.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from utils.logger import get_logger
from modules.backup.onedrive_status import OnedriveStatus
from web.auth import require_auth_api

logger = get_logger("web.routes.backup_status")

router = APIRouter(prefix="/api/backup", tags=["Backup"])

# Singleton-Instanz fuer den Status-Collector
_status_collector = OnedriveStatus()


@router.get("/cloud-status")
async def cloud_backup_status(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Gibt den aktuellen Cloud-Backup-Status als JSON zurueck.

    Erfordert gueltige Authentifizierung (Dashboard-Login).
    Sammelt Log-Daten und optional rclone-Speicherinfo.

    Returns:
        JSON mit Keys: last_upload, overdue, recent_uploads,
        storage, upload_count_7d, timestamp
    """
    try:
        summary = await _status_collector.get_status_summary()

        # Antwort-Zeitstempel hinzufuegen
        summary["timestamp"] = datetime.now(timezone.utc).isoformat()

        logger.debug(
            "Cloud-Status abgerufen von %s",
            current_user.get("username", "Unbekannt"),
        )

        return JSONResponse(content=summary, status_code=200)

    except Exception as e:
        logger.error("Fehler beim Abrufen des Cloud-Backup-Status: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Interner Fehler beim Abrufen des Backup-Status",
                "detail": str(e),
            },
        )
