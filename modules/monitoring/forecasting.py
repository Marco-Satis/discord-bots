"""
F37: Ressourcen-Forecasting — Vorhersage fuer Disk- und RAM-Erschoepfung.

Analysiert die letzten 30 Tage der stats_history-Tabelle per linearer
Regression (pure Python, ohne numpy) und prognostiziert, wann Disk
oder RAM die 100%-Grenze erreichen.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

# Mindestanzahl Datenpunkte fuer eine aussagekraeftige Regression
MIN_DATA_POINTS: int = 10

# Forecast-Fenster in Tagen (wie weit zurueck die Daten gelesen werden)
FORECAST_WINDOW_DAYS: int = 30

# Schwellwerte fuer Warnungen
DISK_WARNING_FREE_PCT: float = 20.0   # Warnung wenn weniger als 20% frei
DISK_CRITICAL_FREE_PCT: float = 10.0  # Kritisch wenn weniger als 10% frei
RAM_WARNING_FREE_PCT: float = 20.0
RAM_CRITICAL_FREE_PCT: float = 10.0

DAYS_WARNING_THRESHOLD: float = 30.0   # Warnung wenn < 30 Tage bis voll
DAYS_CRITICAL_THRESHOLD: float = 7.0   # Kritisch wenn < 7 Tage bis voll


# ---------------------------------------------------------------------------
# Lineare Regression (Pure Python, ohne numpy)
# ---------------------------------------------------------------------------

def _linear_regression(
    x_values: list[float], y_values: list[float]
) -> tuple[float, float]:
    """
    Einfache Least-Squares lineare Regression.

    Berechnet die Steigung (slope) und den y-Achsenabschnitt (intercept)
    einer Ausgleichsgeraden durch die gegebenen Datenpunkte.

    Args:
        x_values: Liste der x-Werte (z.B. Stunden seit Start)
        y_values: Liste der y-Werte (z.B. Prozent-Auslastung)

    Returns:
        Tuple (slope, intercept) — Steigung und y-Achsenabschnitt
    """
    n = len(x_values)
    if n < 2:
        return (0.0, 0.0)

    sum_x = sum(x_values)
    sum_y = sum(y_values)
    sum_xy = sum(x * y for x, y in zip(x_values, y_values))
    sum_x2 = sum(x * x for x in x_values)

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return (0.0, sum_y / n if n else 0.0)

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return (slope, intercept)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parst einen ISO-8601 Timestamp sicher in ein datetime-Objekt.

    Unterstuetzt Formate mit und ohne Zeitzone sowie 'Z'-Suffix.

    Args:
        ts_str: ISO-8601 Timestamp-String

    Returns:
        datetime-Objekt (UTC) oder None bei Fehler
    """
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _compute_forecast(
    x_hours: list[float],
    y_percent: list[float],
    warning_free_pct: float,
    critical_free_pct: float,
) -> dict:
    """
    Fuehrt die lineare Regression durch und berechnet Prognose-Werte.

    Bestimmt den aktuellen Prozentwert, den Trend pro Tag und die
    geschaetzte Restzeit bis 100% erreicht wird.

    Args:
        x_hours:          Stunden seit dem aeltesten Datenpunkt
        y_percent:        Auslastungs-Prozentwerte
        warning_free_pct: Schwelle fuer Warnung (freier Speicher in %)
        critical_free_pct: Schwelle fuer kritisch (freier Speicher in %)

    Returns:
        Dict mit current_percent, trend_per_day, days_until_full, warning, critical
    """
    if not y_percent:
        return {
            "current_percent": 0.0,
            "trend_per_day": 0.0,
            "days_until_full": None,
            "warning": False,
            "critical": False,
        }

    current_percent = y_percent[-1]
    slope, intercept = _linear_regression(x_hours, y_percent)

    # Steigung in Prozent pro Tag umrechnen (slope ist pro Stunde)
    trend_per_day = round(slope * 24.0, 4)

    # Prognose: Wann erreicht die Gerade 100%?
    days_until_full: Optional[float] = None

    if slope > 0:
        # Aktueller Wert auf der Regressionsgerade (letzter x-Wert)
        last_x = x_hours[-1] if x_hours else 0.0
        current_fitted = slope * last_x + intercept

        # Stunden bis 100%
        hours_remaining = (100.0 - current_fitted) / slope
        if hours_remaining > 0:
            days_until_full = round(hours_remaining / 24.0, 1)
        else:
            # Bereits ueber 100% laut Regression — quasi sofort
            days_until_full = 0.0

    # Schwellwert-Pruefung: Warnung und kritisch
    free_pct = 100.0 - current_percent
    warning = False
    critical = False

    # Warnung: Wenig freier Speicher ODER wenig Zeit bis voll
    if free_pct < warning_free_pct:
        warning = True
    if days_until_full is not None and days_until_full < DAYS_WARNING_THRESHOLD:
        warning = True

    # Kritisch: Sehr wenig freier Speicher ODER sehr wenig Zeit bis voll
    if free_pct < critical_free_pct:
        critical = True
    if days_until_full is not None and days_until_full < DAYS_CRITICAL_THRESHOLD:
        critical = True

    return {
        "current_percent": round(current_percent, 1),
        "trend_per_day": trend_per_day,
        "days_until_full": days_until_full,
        "warning": warning,
        "critical": critical,
    }


# ---------------------------------------------------------------------------
# Hauptklasse: ResourceForecaster
# ---------------------------------------------------------------------------

class ResourceForecaster:
    """
    Prognostiziert die Erschoepfung von Disk und RAM basierend auf
    historischen Metriken aus der stats_history-Tabelle.

    Verwendet lineare Regression ueber die letzten 30 Tage um Trends
    zu erkennen und den Zeitpunkt der Erschoepfung vorherzusagen.
    """

    async def _fetch_data(self, column: str) -> tuple[list[float], list[float], int]:
        """
        Laedt historische Werte einer bestimmten Spalte aus der stats_history-Tabelle.

        Liest die letzten 30 Tage, konvertiert Timestamps in Stunden
        seit dem aeltesten Datenpunkt (x-Achse) und gibt die Werte
        zusammen mit der Anzahl gelesener Datenpunkte zurueck.

        Args:
            column: Spaltenname ('disk_percent' oder 'ram_percent')

        Returns:
            Tuple (x_hours, y_values, data_point_count)
        """
        x_hours: list[float] = []
        y_values: list[float] = []

        try:
            db = await get_db()

            # Cutoff berechnen: 30 Tage zurueck
            cutoff = datetime.now(timezone.utc) - timedelta(days=FORECAST_WINDOW_DAYS)
            cutoff_iso = cutoff.isoformat()

            cursor = await db.execute(
                f"SELECT timestamp, {column} FROM stats_history "
                f"WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff_iso,),
            )
            rows = await cursor.fetchall()

            if not rows:
                logger.debug(f"Keine Daten fuer {column} in den letzten {FORECAST_WINDOW_DAYS} Tagen")
                return ([], [], 0)

            # Referenz-Zeitpunkt: aeltester Eintrag
            first_ts = _parse_timestamp(str(rows[0][0]))
            if first_ts is None:
                logger.warning(f"Erster Timestamp nicht parsbar: {rows[0][0]}")
                return ([], [], 0)

            for row in rows:
                ts = _parse_timestamp(str(row[0]))
                value = row[1]

                if ts is None or value is None:
                    continue

                # Stunden seit dem aeltesten Datenpunkt
                delta_hours = (ts - first_ts).total_seconds() / 3600.0
                x_hours.append(delta_hours)
                y_values.append(float(value))

        except Exception as e:
            logger.error(f"Fehler beim Laden der {column}-Daten aus stats_history: {e}")

        return (x_hours, y_values, len(y_values))

    async def get_disk_forecast(self) -> dict:
        """
        Berechnet die Prognose fuer die Festplatten-Auslastung.

        Liest disk_percent-Werte der letzten 30 Tage, fuehrt lineare
        Regression durch und prognostiziert den Zeitpunkt der Erschoepfung.

        Returns:
            Dict mit current_percent, trend_per_day, days_until_full,
            warning, critical und data_points
        """
        x_hours, y_values, count = await self._fetch_data("disk_percent")

        if count < MIN_DATA_POINTS:
            logger.info(
                f"Disk-Forecast: Zu wenig Datenpunkte ({count}/{MIN_DATA_POINTS}), "
                f"Regression nicht moeglich"
            )
            # Aktuellen Wert trotzdem zurueckgeben falls vorhanden
            current = y_values[-1] if y_values else 0.0
            return {
                "current_percent": round(current, 1),
                "trend_per_day": 0.0,
                "days_until_full": None,
                "warning": False,
                "critical": False,
                "data_points": count,
                "insufficient_data": True,
            }

        result = _compute_forecast(
            x_hours, y_values,
            warning_free_pct=DISK_WARNING_FREE_PCT,
            critical_free_pct=DISK_CRITICAL_FREE_PCT,
        )
        result["data_points"] = count
        result["insufficient_data"] = False

        logger.debug(
            f"Disk-Forecast: {result['current_percent']}% belegt, "
            f"Trend {result['trend_per_day']}%/Tag, "
            f"voll in {result['days_until_full']} Tagen"
        )

        return result

    async def get_ram_forecast(self) -> dict:
        """
        Berechnet die Prognose fuer die RAM-Auslastung.

        Gleicher Ansatz wie bei Disk, verwendet aber ram_percent
        aus der stats_history-Tabelle.

        Returns:
            Dict mit current_percent, trend_per_day, days_until_full,
            warning, critical und data_points
        """
        x_hours, y_values, count = await self._fetch_data("ram_percent")

        if count < MIN_DATA_POINTS:
            logger.info(
                f"RAM-Forecast: Zu wenig Datenpunkte ({count}/{MIN_DATA_POINTS}), "
                f"Regression nicht moeglich"
            )
            current = y_values[-1] if y_values else 0.0
            return {
                "current_percent": round(current, 1),
                "trend_per_day": 0.0,
                "days_until_full": None,
                "warning": False,
                "critical": False,
                "data_points": count,
                "insufficient_data": True,
            }

        result = _compute_forecast(
            x_hours, y_values,
            warning_free_pct=RAM_WARNING_FREE_PCT,
            critical_free_pct=RAM_CRITICAL_FREE_PCT,
        )
        result["data_points"] = count
        result["insufficient_data"] = False

        logger.debug(
            f"RAM-Forecast: {result['current_percent']}% belegt, "
            f"Trend {result['trend_per_day']}%/Tag, "
            f"voll in {result['days_until_full']} Tagen"
        )

        return result

    async def get_all_forecasts(self) -> dict:
        """
        Kombinierte Prognose fuer Disk und RAM.

        Fuehrt beide Forecasts aus und gibt ein zusammengefasstes
        Ergebnis zurueck, das auch die Anzahl der verwendeten
        Datenpunkte und das Forecast-Fenster enthaelt.

        Returns:
            Dict mit 'disk', 'ram', 'data_points' und 'forecast_window_days'
        """
        disk_forecast = await self.get_disk_forecast()
        ram_forecast = await self.get_ram_forecast()

        # Datenpunkte: Maximum der beiden (sollten identisch sein,
        # da sie aus derselben Tabelle stammen)
        data_points = max(
            disk_forecast.get("data_points", 0),
            ram_forecast.get("data_points", 0),
        )

        result = {
            "disk": disk_forecast,
            "ram": ram_forecast,
            "data_points": data_points,
            "forecast_window_days": FORECAST_WINDOW_DAYS,
        }

        # Gesamtwarnung loggen falls noetig
        if disk_forecast.get("critical") or ram_forecast.get("critical"):
            logger.warning(
                "Ressourcen-Forecast KRITISCH — "
                f"Disk: {disk_forecast.get('days_until_full')} Tage, "
                f"RAM: {ram_forecast.get('days_until_full')} Tage"
            )
        elif disk_forecast.get("warning") or ram_forecast.get("warning"):
            logger.info(
                "Ressourcen-Forecast Warnung — "
                f"Disk: {disk_forecast.get('days_until_full')} Tage, "
                f"RAM: {ram_forecast.get('days_until_full')} Tage"
            )

        return result
