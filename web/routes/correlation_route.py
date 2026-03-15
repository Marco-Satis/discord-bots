"""
F60: Korrelations-API — REST-Endpunkte fuer Korrelations-Analyse und Anomalie-Erkennung.

Stellt die Ergebnisse der CorrelationAnalyzer-Klasse als JSON-API bereit.
Authentifizierung erforderlich (Dashboard-Login).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.analytics.correlation import CorrelationAnalyzer
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.correlation")

router = APIRouter(prefix="/api/analytics", tags=["Correlation"])


def _check_auth(request: Request) -> dict | None:
    """
    Prueft die Authentifizierung. Gibt den User zurueck oder None.

    Args:
        request: FastAPI Request-Objekt

    Returns:
        User-Dict oder None bei fehlender Authentifizierung
    """
    return get_current_user(request)


@router.get("/correlation")
async def correlation_analysis(request: Request):
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
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"},
        )

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
async def current_anomalies(request: Request):
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
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"},
        )

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
