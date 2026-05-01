"""
F60: Korrelations-API — REST-Endpunkte fuer Korrelations-Analyse und Anomalie-Erkennung.

Stellt die Ergebnisse der CorrelationAnalyzer-Klasse als JSON-API bereit.
Authentifizierung erforderlich (Dashboard-Login).
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from modules.analytics.correlation import CorrelationAnalyzer
from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger("web.routes.correlation")

router = APIRouter(
    prefix="/api/analytics",
    tags=["Correlation"],
    dependencies=[Depends(require_auth_api)],
)


@router.get("/correlation")
async def correlation_analysis():
    """
    Vollstaendige Korrelations-Analyse aller Server-Metriken.

    Gibt ein kombiniertes Ergebnis zurueck mit:
    - crash_vs_players: Zusammenhang Spieleranzahl und Crashes
    - ram_vs_playtime: RAM-Trend ueber Sitzungsdauer
    - crash_patterns: Zeitliche Muster (Stunde, Wochentag)
    - anomalies: Aktuelle Anomalien
    - anomaly_count: Anzahl gefundener Anomalien

    Erfordert Dashboard-Authentifizierung.
    """
    try:
        analyzer = CorrelationAnalyzer()
        result = await analyzer.get_full_analysis()

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"Korrelations-Analyse fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Analyse fehlgeschlagen"},
        )


@router.get("/anomalies")
async def current_anomalies():
    """
    Aktuelle Anomalien in RAM- und CPU-Verbrauch.

    Vergleicht die Metriken der letzten Stunde mit historischen
    Durchschnittswerten fuer die aktuelle Spieleranzahl-Klasse.
    Ein Wert gilt als anomal wenn er mehr als 2 Standardabweichungen
    ueber dem Mittelwert liegt.

    Returns:
        JSON mit 'anomalies' (Liste) und 'count' (Anzahl)

    Erfordert Dashboard-Authentifizierung.
    """
    try:
        analyzer = CorrelationAnalyzer()
        anomalies = await analyzer.get_anomalies()

        return JSONResponse(content={
            "anomalies": anomalies,
            "count": len(anomalies),
        })

    except Exception as e:
        logger.error(f"Anomalie-Erkennung fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Anomalie-Erkennung fehlgeschlagen"},
        )
