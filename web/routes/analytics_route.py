"""
Phase 13c: Analytics API — REST-Endpunkte fuer Statistik-Daten.

Liefert historische System- und Server-Metriken als JSON
fuer Chart.js-Visualisierung im Dashboard.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse

from modules.database.db_manager import get_db
from utils.config import MONITOR_DATA_DIR
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# Pfad zur Stats-Historie (JSON-Fallback)
STATS_HISTORY_FILE = MONITOR_DATA_DIR / "stats_history.json"

# Unterstuetzte Zeitraeume und ihre Dauer in Stunden
PERIOD_HOURS = {
    "24h": 24,
    "7d": 7 * 24,
    "30d": 30 * 24,
}


def _load_stats_history_json() -> list[dict]:
    """
    Laedt die Stats-Historie aus der JSON-Datei (Fallback).

    Returns:
        Liste aller Eintraege oder leere Liste bei Fehler
    """
    try:
        if STATS_HISTORY_FILE.exists():
            with open(STATS_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "entries" in data:
                return data["entries"]
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"Fehler beim Laden der Stats-Historie (JSON): {e}")
    return []


async def _load_stats_from_db(period: str) -> list[dict]:
    """
    Laedt die Stats-Historie aus der SQLite-Datenbank.

    Berechnet den Cutoff-Zeitstempel anhand des Zeitraums und
    fragt die Daten direkt gefiltert ab. Bei DB-Fehler wird
    auf die JSON-Datei zurueckgefallen.

    Args:
        period: Zeitraum-String ('24h', '7d', '30d')

    Returns:
        Liste von Eintraegen mit gleicher Struktur wie die JSON-Variante:
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
        logger.warning(f"DB-Abfrage fehlgeschlagen, Fallback auf JSON: {e}")
        all_entries = _load_stats_history_json()
        return _filter_by_period(all_entries, period)


def _filter_by_period(entries: list[dict], period: str) -> list[dict]:
    """
    Filtert Eintraege nach dem angegebenen Zeitraum.

    Args:
        entries: Liste aller Eintraege
        period: Zeitraum-String ('24h', '7d', '30d')

    Returns:
        Gefilterte Liste von Eintraegen innerhalb des Zeitraums
    """
    hours = PERIOD_HOURS.get(period, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_iso = cutoff.isoformat()

    filtered = []
    for entry in entries:
        ts = entry.get("timestamp", "")
        if ts >= cutoff_iso:
            filtered.append(entry)

    return filtered


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


def _check_auth(request: Request) -> Optional[dict]:
    """
    Prueft die Authentifizierung. Gibt den User zurueck oder None.

    Args:
        request: FastAPI Request-Objekt

    Returns:
        User-Dict oder None bei fehlender Authentifizierung
    """
    return get_current_user(request)


@router.get("/system")
async def analytics_system(
    request: Request,
    period: str = Query(default="24h", regex="^(24h|7d|30d)$"),
):
    """
    System-Stats-Historie (CPU, RAM, Disk ueber Zeit).

    Liefert Labels und Datasets fuer Chart.js-Darstellung.

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"}
        )

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
    period: str = Query(default="24h", regex="^(24h|7d|30d)$"),
):
    """
    Server-spezifische Stats (Spieler, Status, Performance).

    Liefert Labels und Datasets fuer einen bestimmten Server.

    Path-Parameter:
        server_id: ID des Servers (z.B. 'satisfactory', 'minecraft')

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"}
        )

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
    period: str = Query(default="24h", regex="^(24h|7d|30d)$"),
):
    """
    Spieleranzahl ueber alle Server ueber Zeit.

    Liefert pro Server ein Dataset mit Spielerzahlen.

    Query-Parameter:
        period: Zeitraum — '24h', '7d' oder '30d'
    """
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"}
        )

    entries = await _load_stats_from_db(period)
    entries = _downsample(entries)

    labels = []
    # Alle Server-IDs sammeln die in den Eintraegen vorkommen
    server_ids: set[str] = set()
    for entry in entries:
        for srv in entry.get("servers", []):
            sid = srv.get("id", "")
            if sid:
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
    user = _check_auth(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"}
        )

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
