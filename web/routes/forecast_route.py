"""
F37: Forecast API — REST-Endpunkt fuer Ressourcen-Prognosen.

Liefert Disk- und RAM-Forecasts als JSON. Berechnet per linearer
Regression ueber die letzten 30 Tage, wann Ressourcen erschoepft sind.
Auth erforderlich (JWT-Cookie).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from modules.monitoring.forecasting import ResourceForecaster
from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger("web.routes.forecast")

router = APIRouter(prefix="/api", tags=["Forecast"])


@router.get("/forecast")
async def get_forecast(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Ressourcen-Prognose fuer Disk und RAM.

    Berechnet anhand der letzten 30 Tage stats_history-Daten
    per linearer Regression, wann Disk oder RAM erschoepft sind.

    Erfordert Authentifizierung (JWT-Cookie).

    Returns:
        JSON mit Disk- und RAM-Forecast, Datenpunkt-Anzahl,
        Forecast-Fenster und Zeitstempel.
        HTTP 401 bei fehlender Authentifizierung.
        HTTP 500 bei internen Fehlern.
    """
    try:
        forecaster = ResourceForecaster()
        result = await forecaster.get_all_forecasts()

        # Zeitstempel der Abfrage anfuegen
        result["timestamp"] = datetime.now(timezone.utc).isoformat()

        logger.debug(
            "Forecast-API aufgerufen — Disk: %s%% (%s Tage), RAM: %s%% (%s Tage)",
            result["disk"].get("current_percent"),
            result["disk"].get("days_until_full"),
            result["ram"].get("current_percent"),
            result["ram"].get("days_until_full"),
        )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error("Fehler in der Forecast-API: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Forecast-Berechnung fehlgeschlagen",
                "detail": str(e),
            },
        )
