"""
Analytics API — REST-Endpunkte fuer Statistik-Daten.

Liefert historische System- und Server-Metriken als JSON
fuer Chart.js-Visualisierung im Dashboard.

Persistenz: SQLite stats_history Tabelle (alleinige Datenquelle)
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from modules.database.db_manager import get_db, get_read_db
from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/analytics",
    tags=["Analytics"],
    dependencies=[Depends(require_auth_api)],
)

# Unterstuetzte Zeitraeume und ihre Dauer in Stunden
PERIOD_HOURS = {
    "24h": 24,
    "7d": 7 * 24,
    "30d": 30 * 24,
}


async def _load_stats_from_db(period: str) -> list[dict]:
    """
    Laedt die Stats-Historie aus der SQLite-Datenbank.

    Berechnet den Cutoff-Zeitstempel anhand des Zeitraums und
    fragt die Daten direkt gefiltert ab.

    Args:
        period: Zeitraum-String ('24h', '7d', '30d')

    Returns:
        Liste von Eintraegen:
        [{"timestamp": ..., "system": {...}, "servers": [...]}, ...]
    """
    hours = PERIOD_HOURS.get(period, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_iso = cutoff.isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT timestamp, cpu_percent, ram_percent, disk_percent, server_data "
            "FROM stats_history "
            "WHERE timestamp >= ? "
            "ORDER BY timestamp ASC",
            (cutoff_iso,),
        )
        rows = await cursor.fetchall()

        entries = []
        for row in rows:
            ts, cpu, ram, disk, server_data_raw = row

            # server_data JSON parsen
            servers = []
            if server_data_raw:
                try:
                    servers = json.loads(server_data_raw)
                except (json.JSONDecodeError, TypeError):
                    servers = []

            entries.append({
                "timestamp": ts,
                "system": {
                    "cpu_percent": cpu or 0,
                    "ram_percent": ram or 0,
                    "disk_percent": disk or 0,
                },
                "servers": servers if isinstance(servers, list) else [],
            })

        return entries

    except Exception as e:
        logger.warning(f"DB-Abfrage fehlgeschlagen: {e}")
        return []


def _format_timestamp_label(timestamp_str: str, period: str) -> str:
    """
    Formatiert einen ISO-Timestamp als lesbares Label fuer Chart.js.

    Args:
        timestamp_str: ISO-8601 Timestamp
        period: Zeitraum (bestimmt das Format)

    Returns:
        Formatierter Zeitstempel-String
    """
    try:
        # ISO-Timestamp parsen (mit oder ohne Zeitzonen-Info)
        if timestamp_str.endswith("Z"):
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        else:
            ts = datetime.fromisoformat(timestamp_str)

        if period == "24h":
            return ts.strftime("%H:%M")
        elif period == "7d":
            return ts.strftime("%a %H:%M")
        else:  # 30d
            return ts.strftime("%d.%m. %H:%M")
    except (ValueError, TypeError):
        return timestamp_str[:16]


def _downsample(entries: list[dict], max_points: int = 200) -> list[dict]:
    """
    Reduziert die Anzahl der Datenpunkte fuer die Chart-Darstellung.
    Waehlt gleichmaessig verteilte Punkte aus der Liste.

    Args:
        entries: Alle gefilterten Eintraege
        max_points: Maximale Anzahl Datenpunkte

    Returns:
        Reduzierte Liste von Eintraegen
    """
    if len(entries) <= max_points:
        return entries

    # Gleichmaessig verteilte Indizes berechnen
    step = len(entries) / max_points
    indices = [int(i * step) for i in range(max_points)]
    # Letzten Eintrag immer einschliessen
    if indices[-1] != len(entries) - 1:
        indices[-1] = len(entries) - 1

    return [entries[i] for i in indices]


@router.get("/system")
async def analytics_system(
    period: str = Query(default="24h", pattern="^(24h|7d|30d)$"),
    ):
    """
    System-Stats-Historie (CPU, RAM, Disk ueber Zeit).

    Liefert Labels und Datasets fuer Chart.js-Darstellung.

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    entries = await _load_stats_from_db(period)
    entries = _downsample(entries)

    labels = []
    cpu_data = []
    ram_data = []
    disk_data = []

    for entry in entries:
        labels.append(_format_timestamp_label(entry.get("timestamp", ""), period))
        system = entry.get("system", {})
        cpu_data.append(system.get("cpu_percent", 0))
        ram_data.append(system.get("ram_percent", 0))
        disk_data.append(system.get("disk_percent", 0))

    return JSONResponse(content={
        "labels": labels,
        "datasets": [
            {"label": "CPU %", "data": cpu_data},
            {"label": "RAM %", "data": ram_data},
            {"label": "Disk %", "data": disk_data},
        ],
        "period": period,
        "total_points": len(labels),
    })


@router.get("/server/{server_id}")
async def analytics_server(
    request: Request,
    server_id: str,
    period: str = Query(default="24h", pattern="^(24h|7d|30d)$"),
    ):
    """
    Server-spezifische Stats (Spieler, Status, Performance).

    Liefert Labels und Datasets fuer einen bestimmten Server.

    Path-Parameter:
        server_id: ID des Servers (z.B. 'satisfactory', 'minecraft')

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    entries = await _load_stats_from_db(period)
    entries = _downsample(entries)

    labels = []
    player_data = []
    cpu_data = []
    ram_data = []
    status_data = []

    for entry in entries:
        labels.append(_format_timestamp_label(entry.get("timestamp", ""), period))

        # Server-Daten aus dem Eintrag extrahieren
        server_info = {}
        for srv in entry.get("servers", []):
            if srv.get("id") == server_id:
                server_info = srv
                break

        player_data.append(server_info.get("players", 0))
        cpu_data.append(server_info.get("cpu_percent", 0))
        ram_data.append(server_info.get("ram_mb", 0))
        # Status als numerischen Wert: running=1, stopped=0, unknown=-1
        status_str = server_info.get("status", "unknown")
        if status_str == "running":
            status_data.append(1)
        elif status_str == "stopped":
            status_data.append(0)
        else:
            status_data.append(-1)

    return JSONResponse(content={
        "labels": labels,
        "datasets": [
            {"label": "Spieler", "data": player_data},
            {"label": "CPU %", "data": cpu_data},
            {"label": "RAM (MB)", "data": ram_data},
            {"label": "Status", "data": status_data},
        ],
        "server_id": server_id,
        "period": period,
        "total_points": len(labels),
    })


@router.get("/players")
async def analytics_players(
    request: Request,
    period: str = Query(default="24h", pattern="^(24h|7d|30d)$"),
    ):
    """
    Spieleranzahl ueber alle Server ueber Zeit.

    Liefert pro Server ein Dataset mit Spielerzahlen.

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    entries = await _load_stats_from_db(period)
    entries = _downsample(entries)

    # Nicht-Gameserver-IDs aus der Auswertung ausschliessen (ssl-Check, bot-Status etc.)
    EXCLUDE_IDS = {"ssl", "bot", "bot_status"}

    labels = []
    # Alle Server-IDs sammeln die in den Eintraegen vorkommen
    server_ids: set[str] = set()
    for entry in entries:
        for srv in entry.get("servers", []):
            sid = srv.get("id", "")
            if sid and sid not in EXCLUDE_IDS:
                server_ids.add(sid)

    # Daten pro Server sammeln
    server_data: dict[str, list[int]] = {sid: [] for sid in sorted(server_ids)}
    total_data: list[int] = []

    for entry in entries:
        labels.append(_format_timestamp_label(entry.get("timestamp", ""), period))

        # Server-Lookup fuer diesen Eintrag erstellen
        srv_map = {}
        for srv in entry.get("servers", []):
            sid = srv.get("id", "")
            if sid:
                srv_map[sid] = srv

        entry_total = 0
        for sid in sorted(server_ids):
            player_count = srv_map.get(sid, {}).get("players", 0)
            server_data[sid].append(player_count)
            entry_total += player_count

        total_data.append(entry_total)

    # Datasets erstellen: ein Dataset pro Server + Gesamt
    datasets = []
    for sid in sorted(server_ids):
        datasets.append({
            "label": sid.capitalize(),
            "data": server_data[sid],
        })

    # Gesamt-Spieler als zusaetzliches Dataset
    datasets.append({
        "label": "Gesamt",
        "data": total_data,
    })

    return JSONResponse(content={
        "labels": labels,
        "datasets": datasets,
        "period": period,
        "total_points": len(labels),
    })


@router.get("/summary")
async def analytics_summary(request: Request):
    """
    Schnelle Zusammenfassung: Spitzen-Spieler, Durchschnitts-CPU,
    Gesamt-Uptime usw.

    Berechnet Kennzahlen ueber die letzten 24 Stunden.
    """
    entries_24h = await _load_stats_from_db("24h")
    entries_7d = await _load_stats_from_db("7d")

    summary = {
        "entries_24h": len(entries_24h),
        "entries_7d": len(entries_7d),
    }

    # --- 24h System-Durchschnitte ---
    if entries_24h:
        cpu_values = [e.get("system", {}).get("cpu_percent", 0) for e in entries_24h]
        ram_values = [e.get("system", {}).get("ram_percent", 0) for e in entries_24h]
        disk_values = [e.get("system", {}).get("disk_percent", 0) for e in entries_24h]

        summary["avg_cpu_24h"] = round(sum(cpu_values) / len(cpu_values), 1)
        summary["avg_ram_24h"] = round(sum(ram_values) / len(ram_values), 1)
        summary["avg_disk_24h"] = round(sum(disk_values) / len(disk_values), 1)
        summary["peak_cpu_24h"] = round(max(cpu_values), 1)
        summary["peak_ram_24h"] = round(max(ram_values), 1)
    else:
        summary["avg_cpu_24h"] = 0
        summary["avg_ram_24h"] = 0
        summary["avg_disk_24h"] = 0
        summary["peak_cpu_24h"] = 0
        summary["peak_ram_24h"] = 0

    # --- Spieler-Statistiken (24h) ---
    peak_players = 0
    total_player_time = 0  # Summe aller Spieler ueber alle Eintraege
    server_uptime: dict[str, int] = {}  # Anzahl "running"-Eintraege pro Server

    for entry in entries_24h:
        entry_players = 0
        for srv in entry.get("servers", []):
            sid = srv.get("id", "unknown")
            players = srv.get("players", 0)
            entry_players += players

            # Uptime zaehlen
            if srv.get("status") == "running":
                server_uptime[sid] = server_uptime.get(sid, 0) + 1

        if entry_players > peak_players:
            peak_players = entry_players
        total_player_time += entry_players

    summary["peak_players_24h"] = peak_players
    summary["avg_players_24h"] = round(
        total_player_time / len(entries_24h), 1
    ) if entries_24h else 0

    # Uptime-Prozent pro Server berechnen
    server_uptime_pct = {}
    for sid, running_count in server_uptime.items():
        pct = round((running_count / len(entries_24h)) * 100, 1) if entries_24h else 0
        server_uptime_pct[sid] = pct

    summary["server_uptime_24h"] = server_uptime_pct

    # --- 7-Tage Spitzenwerte ---
    if entries_7d:
        peak_players_7d = 0
        for entry in entries_7d:
            entry_total = sum(
                srv.get("players", 0) for srv in entry.get("servers", [])
            )
            if entry_total > peak_players_7d:
                peak_players_7d = entry_total
        summary["peak_players_7d"] = peak_players_7d

        cpu_7d = [e.get("system", {}).get("cpu_percent", 0) for e in entries_7d]
        summary["peak_cpu_7d"] = round(max(cpu_7d), 1)
    else:
        summary["peak_players_7d"] = 0
        summary["peak_cpu_7d"] = 0

    return JSONResponse(content=summary)


    # ---------------------------------------------------------------------------
    # F57: Erweiterte Dashboard-Analytics — komplexe SQL-basierte Auswertungen
    # ---------------------------------------------------------------------------


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parst einen ISO-8601-Timestamp-String zu einem datetime-Objekt.

    Unterstuetzt sowohl Timestamps mit 'Z'-Suffix als auch mit
    expliziter Zeitzonen-Info oder ohne Zeitzone.

    Args:
        ts_str: ISO-8601 formatierter Zeitstempel

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


@router.get("/heatmap")
async def analytics_heatmap(request: Request):
    """
    F57: Spieler-Aktivitaets-Heatmap (Stunde x Wochentag).

    Analysiert die letzten 30 Tage und berechnet eine 7x24-Matrix
    mit durchschnittlichen Spielerzahlen pro Wochentag und Stunde.

    Returns:
        JSON mit 'heatmap' (7x24 Matrix), 'max_value' (hoechster Durchschnitt)
    """
    try:
        db = await get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        cursor = await db.execute(
            "SELECT timestamp, server_data FROM stats_history "
            "WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Heatmap DB-Abfrage fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Datenbankfehler"}
        )

    # Summen- und Zaehler-Matrizen: [weekday 0-6][hour 0-23]
    # Wochentag 0 = Montag, 6 = Sonntag (Python-Konvention)
    totals = [[0.0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]

    for ts_str, server_data_raw in rows:
        ts = _parse_timestamp(ts_str)
        if ts is None:
            continue

        weekday = ts.weekday()  # 0=Montag, 6=Sonntag
        hour = ts.hour

        # Spieler aller Server summieren
        servers = _parse_server_data(server_data_raw)
        total_players = sum(srv.get("players", 0) for srv in servers)

        totals[weekday][hour] += total_players
        counts[weekday][hour] += 1

    # Durchschnitts-Matrix berechnen
    heatmap = []
    max_value = 0.0
    for wd in range(7):
        row = []
        for hr in range(24):
            if counts[wd][hr] > 0:
                avg = round(totals[wd][hr] / counts[wd][hr], 2)
            else:
                avg = 0.0
            row.append(avg)
            if avg > max_value:
                max_value = avg
        heatmap.append(row)

    return JSONResponse(content={
        "heatmap": heatmap,
        "max_value": max_value,
    })


@router.get("/peaks")
async def analytics_peaks(request: Request):
    """
    F57: Historische Spitzenwert-Analyse.

    Findet die Top-10-Zeitpunkte mit den meisten gleichzeitigen Spielern
    ueber alle Server hinweg aus der gesamten Statistik-Historie.

    Returns:
        JSON mit 'peaks' — Liste der 10 Zeitpunkte mit hoechster Spielerzahl,
        jeweils mit Timestamp, Gesamt-Spielerzahl und Server-Aufschluesselung
    """
    # F57-Fix (audit 2026-05-17, perf.md F3): Window + LIMIT statt Full-Table-Scan.
    # 24k+ Zeilen wachsen ~525k/Jahr — ohne Bounds wird /peaks linear langsamer.
    # Top-10 aus letzten 90 Tagen + Hard-Cap 5000 Zeilen reicht praktisch fuer alle
    # Spitzenwert-Reports; aeltere Peaks sind operativ irrelevant.
    PEAKS_WINDOW_DAYS = 90
    PEAKS_HARD_CAP = 5000
    window_start = (datetime.now() - timedelta(days=PEAKS_WINDOW_DAYS)).isoformat()

    try:
        # Read-Pool nutzen (audit-fix 2026-05-17, perf.md F2):
        # SELECT-only Pfad — vermeidet Serialisierung der Write-Connection.
        db = await get_read_db()
        cursor = await db.execute(
            "SELECT timestamp, server_data FROM stats_history "
            "WHERE timestamp >= ? "
            "ORDER BY timestamp DESC "
            "LIMIT ?",
            (window_start, PEAKS_HARD_CAP),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Peaks DB-Abfrage fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Datenbankfehler"}
        )

    # Alle Eintraege mit Gesamtspielerzahl berechnen
    scored_entries = []
    for ts_str, server_data_raw in rows:
        servers = _parse_server_data(server_data_raw)
        if not servers:
            continue

        # Pro Server: Name/ID und Spieleranzahl
        server_dict = {}
        total_players = 0
        for srv in servers:
            sid = srv.get("id", "unknown")
            players = srv.get("players", 0)
            server_dict[sid] = players
            total_players += players

        scored_entries.append({
            "timestamp": ts_str,
            "total_players": total_players,
            "servers": server_dict,
        })

    # Nach Gesamtspielerzahl absteigend sortieren, Top 10 nehmen
    scored_entries.sort(key=lambda x: x["total_players"], reverse=True)
    peaks = scored_entries[:10]

    return JSONResponse(content={
        "peaks": peaks,
    })


@router.get("/trends")
async def analytics_trends(request: Request):
    """
    F57: Woechentlicher Vergleich (diese Woche vs. letzte Woche).

    Berechnet fuer beide Wochen: durchschnittliche Spieler, CPU, RAM,
    Anzahl Events und Uptime-Prozent. Zeigt die prozentuale Veraenderung.

    Returns:
        JSON mit 'this_week', 'last_week' und 'changes' (prozentuale Aenderung)
    """
    now = datetime.now(timezone.utc)

    # Zeitraum-Grenzen berechnen: diese Woche (letzte 7 Tage) und letzte Woche (7-14 Tage)
    this_week_start = (now - timedelta(days=7)).isoformat()
    last_week_start = (now - timedelta(days=14)).isoformat()
    this_week_end = now.isoformat()
    last_week_end = this_week_start  # Ende letzte Woche = Anfang diese Woche

    try:
        db = await get_db()

        # Stats fuer diese Woche laden
        cursor = await db.execute(
            "SELECT timestamp, cpu_percent, ram_percent, server_data "
            "FROM stats_history WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp ASC",
            (this_week_start, this_week_end),
        )
        this_week_rows = await cursor.fetchall()

        # Stats fuer letzte Woche laden
        cursor = await db.execute(
            "SELECT timestamp, cpu_percent, ram_percent, server_data "
            "FROM stats_history WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp ASC",
            (last_week_start, last_week_end),
        )
        last_week_rows = await cursor.fetchall()

        # Events zaehlen fuer beide Wochen
        cursor = await db.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp >= ? AND timestamp < ?",
            (this_week_start, this_week_end),
        )
        this_week_events = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM events WHERE timestamp >= ? AND timestamp < ?",
            (last_week_start, last_week_end),
        )
        last_week_events = (await cursor.fetchone())[0]

    except Exception as e:
        logger.error(f"Trends DB-Abfrage fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Datenbankfehler"}
        )

    def _calc_week_stats(rows: list) -> dict:
        """Berechnet Wochen-Statistiken aus den DB-Zeilen."""
        if not rows:
            return {
                "avg_players": 0.0,
                "avg_cpu": 0.0,
                "avg_ram": 0.0,
                "total_entries": 0,
                "uptime_percent": 0.0,
            }

        total_players = 0.0
        total_cpu = 0.0
        total_ram = 0.0
        uptime_entries = 0  # Eintraege in denen mind. 1 Server running ist

        for ts_str, cpu, ram, server_data_raw in rows:
            total_cpu += cpu or 0
            total_ram += ram or 0

            servers = _parse_server_data(server_data_raw)
            entry_players = sum(srv.get("players", 0) for srv in servers)
            total_players += entry_players

            # Uptime: mindestens ein Server laeuft
            any_running = any(
                srv.get("status") == "running" for srv in servers
            )
            if any_running:
                uptime_entries += 1

        count = len(rows)
        return {
            "avg_players": round(total_players / count, 2),
            "avg_cpu": round(total_cpu / count, 2),
            "avg_ram": round(total_ram / count, 2),
            "total_entries": count,
            "uptime_percent": round((uptime_entries / count) * 100, 1),
        }

    this_week_stats = _calc_week_stats(this_week_rows)
    this_week_stats["total_events"] = this_week_events

    last_week_stats = _calc_week_stats(last_week_rows)
    last_week_stats["total_events"] = last_week_events

    # Prozentuale Veraenderungen berechnen
    def _calc_change(current: float, previous: float) -> str:
        """Berechnet die prozentuale Veraenderung als String."""
        if previous == 0:
            if current == 0:
                return "0%"
            return "+100%"  # Von 0 auf etwas = +100%
        change = ((current - previous) / previous) * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{round(change, 1)}%"

    changes = {
        "players": _calc_change(
            this_week_stats["avg_players"], last_week_stats["avg_players"]
        ),
        "cpu": _calc_change(
            this_week_stats["avg_cpu"], last_week_stats["avg_cpu"]
        ),
        "ram": _calc_change(
            this_week_stats["avg_ram"], last_week_stats["avg_ram"]
        ),
        "events": _calc_change(
            this_week_stats["total_events"], last_week_stats["total_events"]
        ),
        "uptime": _calc_change(
            this_week_stats["uptime_percent"], last_week_stats["uptime_percent"]
        ),
    }

    return JSONResponse(content={
        "this_week": this_week_stats,
        "last_week": last_week_stats,
        "changes": changes,
    })


@router.get("/server-comparison")
async def analytics_server_comparison(request: Request):
    """
    F57: Server-Vergleich nebeneinander.

    Berechnet fuer jeden Server der letzten 30 Tage:
    Uptime-Prozent, durchschnittliche Spieler und Spitzen-Spielerzahl.

    Returns:
        JSON mit 'servers' — Liste aller Server mit Vergleichs-Metriken
    """
    try:
        db = await get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        cursor = await db.execute(
            "SELECT timestamp, server_data FROM stats_history "
            "WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Server-Vergleich DB-Abfrage fehlgeschlagen: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Datenbankfehler"}
        )

    # Gesamtzahl der Eintraege fuer Uptime-Berechnung
    total_entries = len(rows)

    # Akkumulatoren pro Server
    # Struktur: {server_id: {"name": str, "total_players": int, "peak": int,
    #            "running_count": int, "entry_count": int}}
    server_stats: dict[str, dict] = {}

    for ts_str, server_data_raw in rows:
        servers = _parse_server_data(server_data_raw)

        for srv in servers:
            sid = srv.get("id", "")
            if not sid:
                continue

            if sid not in server_stats:
                server_stats[sid] = {
                    "name": srv.get("name", sid),
                    "total_players": 0,
                    "peak_players": 0,
                    "running_count": 0,
                    "entry_count": 0,
                }

            stats = server_stats[sid]
            players = srv.get("players", 0)
            stats["total_players"] += players
            stats["entry_count"] += 1

            if players > stats["peak_players"]:
                stats["peak_players"] = players

            if srv.get("status") == "running":
                stats["running_count"] += 1

    # Ergebnis-Liste aufbauen
    result = []
    for sid, stats in sorted(server_stats.items()):
        entry_count = stats["entry_count"]
        avg_players = round(
            stats["total_players"] / entry_count, 2
        ) if entry_count > 0 else 0.0

        # Uptime bezogen auf Gesamt-Eintraege (wie oft war der Server
        # in den 30 Tagen als "running" verzeichnet)
        uptime_pct = round(
            (stats["running_count"] / total_entries) * 100, 1
        ) if total_entries > 0 else 0.0

        result.append({
            "id": sid,
            "name": stats["name"],
            "uptime_percent": uptime_pct,
            "avg_players": avg_players,
            "peak_players": stats["peak_players"],
        })

    return JSONResponse(content={
        "servers": result,
    })
