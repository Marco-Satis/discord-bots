"""
F60: Korrelations-Analyse — automatische Erkennung von Zusammenhängen
zwischen Server-Metriken.

Analysiert Crash-Muster, RAM/Spielzeit-Korrelationen und erkennt
Anomalien (ungewöhnlich hoher RAM-/CPU-Verbrauch für die aktuelle
Spieleranzahl) per Pearson-Korrelation und Standardabweichung.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("analytics.correlation")

# Event-Typen die als Crash/Neustart gewertet werden
CRASH_EVENT_TYPES = ("crash", "auto_restart", "server_restart", "error")

# Wochentage (Python weekday() 0=Montag, 6=Sonntag)
WEEKDAY_NAMES = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}

# Schwellwert fuer Anomalie-Erkennung (Standardabweichungen)
ANOMALY_THRESHOLD_SIGMA = 2.0

# Zeitraum fuer historische Vergleichsdaten (Tage)
HISTORY_DAYS = 30

# Zeitraum fuer aktuelle Daten bei Anomalie-Check (Stunden)
RECENT_HOURS = 1


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """
    Berechnet den Pearson-Korrelationskoeffizienten zwischen zwei Listen.

    Gibt einen Wert zwischen -1.0 (perfekt negativ korreliert) und
    +1.0 (perfekt positiv korreliert) zurück. Bei weniger als 3
    Datenpunkten oder konstanten Werten wird 0.0 zurückgegeben.

    Args:
        x: Erste Werteliste
        y: Zweite Werteliste (gleiche Länge wie x)

    Returns:
        Pearson-Korrelationskoeffizient als float
    """
    n = len(x)
    if n < 3:
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parst einen ISO-8601-Timestamp-String.

    Unterstützt Timestamps mit 'Z'-Suffix, mit expliziter Zeitzone
    oder ohne Zeitzone.

    Args:
        ts_str: ISO-formatierter Zeitstempel

    Returns:
        datetime-Objekt oder None bei Parse-Fehler
    """
    try:
        if ts_str.endswith("Z"):
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_server_data(raw: Optional[str]) -> list[dict]:
    """
    Parst den server_data JSON-String aus der Datenbank.

    Args:
        raw: JSON-String oder None

    Returns:
        Liste von Server-Dicts oder leere Liste bei Fehler
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _interpret_correlation(r: float) -> str:
    """
    Gibt eine textuelle Interpretation des Korrelationskoeffizienten.

    Args:
        r: Pearson-Korrelationskoeffizient (-1.0 bis +1.0)

    Returns:
        Beschreibender String (z.B. 'stark positiv')
    """
    abs_r = abs(r)
    if abs_r < 0.1:
        return "keine Korrelation"
    elif abs_r < 0.3:
        direction = "positiv" if r > 0 else "negativ"
        return f"schwach {direction}"
    elif abs_r < 0.7:
        direction = "positiv" if r > 0 else "negativ"
        return f"mittel {direction}"
    else:
        direction = "positiv" if r > 0 else "negativ"
        return f"stark {direction}"


class CorrelationAnalyzer:
    """
    Analysiert Korrelationen zwischen Server-Metriken.

    Untersucht Zusammenhänge zwischen:
    - Crash-Häufigkeit und Spieleranzahl
    - RAM-Verbrauch und Sitzungsdauer
    - Crash-Muster nach Tageszeit und Wochentag
    - Anomalien in RAM/CPU relativ zur Spieleranzahl
    """

    async def analyze_crash_vs_players(self) -> dict:
        """
        Analysiert ob Server häufiger abstürzen wenn mehr Spieler online sind.

        Vergleicht die durchschnittliche Spieleranzahl zum Zeitpunkt eines
        Crashs mit der Gesamtdurchschnitts-Spieleranzahl. Berechnet den
        Pearson-Korrelationskoeffizienten zwischen Spieleranzahl und
        Crash-Wahrscheinlichkeit.

        Returns:
            Dict mit avg_players_at_crash, avg_players_overall, correlation,
            crash_count und data_points
        """
        try:
            db = await get_db()

            # Cutoff für den Analysezeitraum
            cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()

            # Crash-Events abrufen
            placeholders = ", ".join("?" for _ in CRASH_EVENT_TYPES)
            cursor = await db.execute(
                f"SELECT timestamp FROM events "  # nosec B608 - placeholders sind nur "?,?,?"
                f"WHERE event_type IN ({placeholders}) "
                f"AND timestamp >= ? "
                f"ORDER BY timestamp ASC",
                (*CRASH_EVENT_TYPES, cutoff),
            )
            crash_rows = await cursor.fetchall()
            crash_timestamps = [str(row[0]) for row in crash_rows if row[0]]

            # Stats-Historie für den gleichen Zeitraum laden
            cursor = await db.execute(
                "SELECT timestamp, server_data FROM stats_history "
                "WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff,),
            )
            stats_rows = await cursor.fetchall()

        except Exception as e:
            logger.error(f"DB-Abfrage fehlgeschlagen (crash_vs_players): {e}")
            return {
                "avg_players_at_crash": 0.0,
                "avg_players_overall": 0.0,
                "correlation": "keine Daten",
                "crash_count": 0,
                "data_points": 0,
            }

        if not stats_rows:
            return {
                "avg_players_at_crash": 0.0,
                "avg_players_overall": 0.0,
                "correlation": "keine Daten",
                "crash_count": 0,
                "data_points": 0,
            }

        # Spieleranzahl-Zeitreihe aufbauen
        player_series: list[tuple[str, int]] = []
        for ts_str, server_data_raw in stats_rows:
            servers = _parse_server_data(server_data_raw)
            total_players = sum(srv.get("players", 0) for srv in servers)
            player_series.append((str(ts_str), total_players))

        # Gesamtdurchschnitt berechnen
        all_player_counts = [p for _, p in player_series]
        avg_overall = sum(all_player_counts) / len(all_player_counts) if all_player_counts else 0.0

        # Spieleranzahl zum Crash-Zeitpunkt ermitteln (nächstgelegener Stats-Eintrag)
        crash_player_counts = []
        for crash_ts in crash_timestamps:
            # Nächsten Stats-Eintrag vor/nach dem Crash finden
            closest_players = self._find_closest_player_count(crash_ts, player_series)
            if closest_players is not None:
                crash_player_counts.append(closest_players)

        avg_at_crash = (
            sum(crash_player_counts) / len(crash_player_counts)
            if crash_player_counts else 0.0
        )

        # Pearson-Korrelation: Spieleranzahl vs. Crash-Indikator (1/0)
        # Für jeden Stats-Eintrag: war in der Nähe ein Crash?
        player_values = []
        crash_indicators = []
        for ts_str, players in player_series:
            player_values.append(float(players))
            # Crash innerhalb von 10 Minuten?
            is_crash = self._is_near_crash(ts_str, crash_timestamps, minutes=10)
            crash_indicators.append(1.0 if is_crash else 0.0)

        r = _pearson_correlation(player_values, crash_indicators)

        return {
            "avg_players_at_crash": round(avg_at_crash, 2),
            "avg_players_overall": round(avg_overall, 2),
            "correlation": _interpret_correlation(r),
            "correlation_r": round(r, 4),
            "crash_count": len(crash_timestamps),
            "data_points": len(player_series),
        }

    async def analyze_ram_vs_playtime(self) -> dict:
        """
        Analysiert ob der RAM-Verbrauch linear mit der Sitzungsdauer wächst.

        Berechnet die Korrelation zwischen der Laufzeit (Stunden seit
        erstem Eintrag) und dem RAM-Verbrauch. Ein hoher positiver Wert
        deutet auf ein Memory-Leak hin.

        Returns:
            Dict mit correlation_r, correlation (Text), data_points,
            ram_start_pct, ram_end_pct und trend
        """
        try:
            db = await get_db()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()

            cursor = await db.execute(
                "SELECT timestamp, ram_percent FROM stats_history "
                "WHERE timestamp >= ? AND ram_percent IS NOT NULL "
                "ORDER BY timestamp ASC",
                (cutoff,),
            )
            rows = await cursor.fetchall()

        except Exception as e:
            logger.error(f"DB-Abfrage fehlgeschlagen (ram_vs_playtime): {e}")
            return {
                "correlation_r": 0.0,
                "correlation": "keine Daten",
                "data_points": 0,
                "ram_start_pct": 0.0,
                "ram_end_pct": 0.0,
                "trend": "unbekannt",
            }

        if len(rows) < 3:
            return {
                "correlation_r": 0.0,
                "correlation": "zu wenig Daten",
                "data_points": len(rows),
                "ram_start_pct": 0.0,
                "ram_end_pct": 0.0,
                "trend": "unbekannt",
            }

        # Zeitreihe: Stunden seit erstem Eintrag und RAM-%
        first_ts = _parse_timestamp(str(rows[0][0]))
        if first_ts is None:
            return {
                "correlation_r": 0.0,
                "correlation": "Timestamp-Fehler",
                "data_points": 0,
                "ram_start_pct": 0.0,
                "ram_end_pct": 0.0,
                "trend": "unbekannt",
            }

        time_values: list[float] = []
        ram_values: list[float] = []

        for ts_str, ram_pct in rows:
            ts = _parse_timestamp(str(ts_str))
            if ts is None or ram_pct is None:
                continue
            hours_elapsed = (ts - first_ts).total_seconds() / 3600.0
            time_values.append(hours_elapsed)
            ram_values.append(float(ram_pct))

        r = _pearson_correlation(time_values, ram_values)

        # Trend bestimmen
        if r > 0.5:
            trend = "steigend (mögliches Memory-Leak)"
        elif r > 0.2:
            trend = "leicht steigend"
        elif r < -0.2:
            trend = "fallend"
        else:
            trend = "stabil"

        return {
            "correlation_r": round(r, 4),
            "correlation": _interpret_correlation(r),
            "data_points": len(time_values),
            "ram_start_pct": round(ram_values[0], 1) if ram_values else 0.0,
            "ram_end_pct": round(ram_values[-1], 1) if ram_values else 0.0,
            "trend": trend,
        }

    async def analyze_crash_patterns(self) -> dict:
        """
        Erkennt Muster in Crash-Zeitpunkten (Tageszeit, Wochentag).

        Gruppiert Crash-Events nach Stunde (0-23) und Wochentag (Mo-So)
        und identifiziert die häufigsten Crash-Zeitpunkte.

        Returns:
            Dict mit by_hour (Stunde -> Anzahl), by_weekday (Wochentag -> Anzahl),
            peak_hour, peak_weekday und total_crashes
        """
        try:
            db = await get_db()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()

            placeholders = ", ".join("?" for _ in CRASH_EVENT_TYPES)
            cursor = await db.execute(
                f"SELECT timestamp FROM events "  # nosec B608 - placeholders sind nur "?,?,?"
                f"WHERE event_type IN ({placeholders}) "
                f"AND timestamp >= ? "
                f"ORDER BY timestamp ASC",
                (*CRASH_EVENT_TYPES, cutoff),
            )
            rows = await cursor.fetchall()

        except Exception as e:
            logger.error(f"DB-Abfrage fehlgeschlagen (crash_patterns): {e}")
            return {
                "by_hour": {},
                "by_weekday": {},
                "peak_hour": None,
                "peak_weekday": None,
                "total_crashes": 0,
            }

        # Zähler für Stunden und Wochentage
        by_hour: dict[int, int] = {h: 0 for h in range(24)}
        by_weekday: dict[int, int] = {d: 0 for d in range(7)}

        total = 0
        for row in rows:
            ts = _parse_timestamp(str(row[0]))
            if ts is None:
                continue
            by_hour[ts.hour] += 1
            by_weekday[ts.weekday()] += 1
            total += 1

        # Spitzen-Stunde und Spitzen-Wochentag ermitteln
        peak_hour = max(by_hour, key=by_hour.get) if total > 0 else None
        peak_weekday_idx = max(by_weekday, key=by_weekday.get) if total > 0 else None

        # Wochentag-Ergebnis mit Namen
        by_weekday_named = {
            WEEKDAY_NAMES[k]: v for k, v in by_weekday.items()
        }
        peak_weekday_name = WEEKDAY_NAMES.get(peak_weekday_idx) if peak_weekday_idx is not None else None

        return {
            "by_hour": by_hour,
            "by_weekday": by_weekday_named,
            "peak_hour": peak_hour,
            "peak_weekday": peak_weekday_name,
            "total_crashes": total,
        }

    async def get_anomalies(self) -> list[dict]:
        """
        Erkennt ungewöhnliche RAM-/CPU-Werte für die aktuelle Spieleranzahl.

        Vergleicht die Metriken der letzten Stunde mit historischen
        Durchschnittswerten. Ein Wert gilt als anomal wenn er mehr als
        2 Standardabweichungen über dem Mittelwert für die gleiche
        Spieleranzahl-Klasse liegt.

        Returns:
            Liste von Anomalie-Dicts mit timestamp, metric, value,
            expected_mean, std_dev und player_count
        """
        try:
            db = await get_db()

            # Historische Daten laden (letzten 30 Tage)
            cutoff_history = (
                datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
            ).isoformat()
            cursor = await db.execute(
                "SELECT timestamp, cpu_percent, ram_percent, server_data "
                "FROM stats_history "
                "WHERE timestamp >= ? "
                "ORDER BY timestamp ASC",
                (cutoff_history,),
            )
            history_rows = await cursor.fetchall()

            # Aktuelle Daten laden (letzte Stunde)
            cutoff_recent = (
                datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)
            ).isoformat()
            cursor = await db.execute(
                "SELECT timestamp, cpu_percent, ram_percent, server_data "
                "FROM stats_history "
                "WHERE timestamp >= ? "
                "ORDER BY timestamp DESC",
                (cutoff_recent,),
            )
            recent_rows = await cursor.fetchall()

        except Exception as e:
            logger.error(f"DB-Abfrage fehlgeschlagen (anomalies): {e}")
            return []

        if not history_rows or not recent_rows:
            return []

        # Historische Werte nach Spieleranzahl-Klasse gruppieren
        # Klassen: 0, 1-2, 3-5, 6-10, 11-20, 21+
        history_by_class: dict[str, dict[str, list[float]]] = {}

        for ts_str, cpu, ram, server_data_raw in history_rows:
            servers = _parse_server_data(server_data_raw)
            total_players = sum(srv.get("players", 0) for srv in servers)
            player_class = self._get_player_class(total_players)

            if player_class not in history_by_class:
                history_by_class[player_class] = {"cpu": [], "ram": []}

            if cpu is not None:
                history_by_class[player_class]["cpu"].append(float(cpu))
            if ram is not None:
                history_by_class[player_class]["ram"].append(float(ram))

        # Mittelwert und Standardabweichung pro Klasse berechnen
        class_stats: dict[str, dict[str, tuple[float, float]]] = {}
        for player_class, metrics in history_by_class.items():
            class_stats[player_class] = {}
            for metric_name, values in metrics.items():
                if len(values) < 5:
                    continue
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std = variance ** 0.5
                class_stats[player_class][metric_name] = (mean, std)

        # Aktuelle Werte gegen historische Statistiken prüfen
        anomalies: list[dict] = []

        for ts_str, cpu, ram, server_data_raw in recent_rows:
            servers = _parse_server_data(server_data_raw)
            total_players = sum(srv.get("players", 0) for srv in servers)
            player_class = self._get_player_class(total_players)

            if player_class not in class_stats:
                continue

            stats = class_stats[player_class]

            # CPU-Anomalie prüfen
            if cpu is not None and "cpu" in stats:
                mean, std = stats["cpu"]
                if std > 0 and float(cpu) > mean + ANOMALY_THRESHOLD_SIGMA * std:
                    anomalies.append({
                        "timestamp": str(ts_str),
                        "metric": "CPU",
                        "value": round(float(cpu), 1),
                        "expected_mean": round(mean, 1),
                        "std_dev": round(std, 1),
                        "sigma": round((float(cpu) - mean) / std, 2),
                        "player_count": total_players,
                        "player_class": player_class,
                    })

            # RAM-Anomalie prüfen
            if ram is not None and "ram" in stats:
                mean, std = stats["ram"]
                if std > 0 and float(ram) > mean + ANOMALY_THRESHOLD_SIGMA * std:
                    anomalies.append({
                        "timestamp": str(ts_str),
                        "metric": "RAM",
                        "value": round(float(ram), 1),
                        "expected_mean": round(mean, 1),
                        "std_dev": round(std, 1),
                        "sigma": round((float(ram) - mean) / std, 2),
                        "player_count": total_players,
                        "player_class": player_class,
                    })

        logger.info(
            f"Anomalie-Check abgeschlossen: {len(anomalies)} Anomalien "
            f"in {len(recent_rows)} aktuellen Einträgen gefunden"
        )
        return anomalies

    async def get_full_analysis(self) -> dict:
        """
        Führt alle Korrelations-Analysen durch und gibt das kombinierte
        Ergebnis zurück.

        Ruft alle Einzel-Analysen parallel auf und fasst sie in einem
        Dict zusammen:
        - crash_vs_players: Zusammenhang Spieleranzahl/Crashes
        - ram_vs_playtime: RAM-Trend über Zeit
        - crash_patterns: Zeitliche Crash-Muster
        - anomalies: Aktuelle Anomalien
        - generated_at: Zeitstempel der Analyse

        Returns:
            Kombiniertes Analyse-Ergebnis als Dict
        """
        crash_players = await self.analyze_crash_vs_players()
        ram_playtime = await self.analyze_ram_vs_playtime()
        crash_patterns = await self.analyze_crash_patterns()
        anomalies = await self.get_anomalies()

        return {
            "crash_vs_players": crash_players,
            "ram_vs_playtime": ram_playtime,
            "crash_patterns": crash_patterns,
            "anomalies": anomalies,
            "anomaly_count": len(anomalies),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    @staticmethod
    def _find_closest_player_count(
        target_ts: str, player_series: list[tuple[str, int]]
    ) -> Optional[int]:
        """
        Findet die Spieleranzahl zum nächstgelegenen Zeitpunkt.

        Sucht in der Spieler-Zeitreihe den Eintrag mit dem geringsten
        zeitlichen Abstand zum Ziel-Timestamp.

        Args:
            target_ts: Ziel-Timestamp (ISO-Format)
            player_series: Liste von (timestamp, player_count) Tupeln

        Returns:
            Spieleranzahl oder None wenn keine Daten vorhanden
        """
        if not player_series:
            return None

        target = _parse_timestamp(target_ts)
        if target is None:
            return None

        best_diff = float("inf")
        best_count = None

        for ts_str, count in player_series:
            ts = _parse_timestamp(ts_str)
            if ts is None:
                continue
            # Naive/Aware-Vergleich: beide auf naive umwandeln
            t1 = target.replace(tzinfo=None)
            t2 = ts.replace(tzinfo=None)
            diff = abs((t1 - t2).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_count = count

        # Nur zurückgeben wenn der nächste Eintrag maximal 30 Minuten entfernt ist
        if best_diff <= 1800:
            return best_count
        return None

    @staticmethod
    def _is_near_crash(
        ts_str: str, crash_timestamps: list[str], minutes: int = 10
    ) -> bool:
        """
        Prüft ob ein Zeitpunkt in der Nähe eines Crashs liegt.

        Args:
            ts_str: Zu prüfender Zeitpunkt (ISO-Format)
            crash_timestamps: Liste von Crash-Zeitstempeln
            minutes: Maximaler Abstand in Minuten

        Returns:
            True wenn ein Crash innerhalb des Zeitfensters liegt
        """
        ts = _parse_timestamp(ts_str)
        if ts is None:
            return False

        threshold = timedelta(minutes=minutes)
        t1 = ts.replace(tzinfo=None)

        for crash_ts_str in crash_timestamps:
            crash_ts = _parse_timestamp(crash_ts_str)
            if crash_ts is None:
                continue
            t2 = crash_ts.replace(tzinfo=None)
            if abs(t1 - t2) <= threshold:
                return True

        return False

    @staticmethod
    def _get_player_class(player_count: int) -> str:
        """
        Ordnet eine Spieleranzahl einer Klasse zu für die Anomalie-Erkennung.

        Klassen:
        - '0': Keine Spieler online
        - '1-2': 1 bis 2 Spieler
        - '3-5': 3 bis 5 Spieler
        - '6-10': 6 bis 10 Spieler
        - '11-20': 11 bis 20 Spieler
        - '21+': Mehr als 20 Spieler

        Args:
            player_count: Aktuelle Spieleranzahl

        Returns:
            Klassen-String
        """
        if player_count <= 0:
            return "0"
        elif player_count <= 2:
            return "1-2"
        elif player_count <= 5:
            return "3-5"
        elif player_count <= 10:
            return "6-10"
        elif player_count <= 20:
            return "11-20"
        else:
            return "21+"
