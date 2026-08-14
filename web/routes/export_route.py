"""
F58: CSV-Export — Download-Endpunkte fuer Dashboard-Daten.

Ermoeglicht den Export von Spieler-Sessions, Events, Stats-Historie,
Audit-Log und Command-Log als CSV-Dateien. Alle Endpunkte erfordern
Authentifizierung und unterstuetzen einen konfigurierbaren Zeitraum.
"""

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from modules.database.db_manager import get_db
from utils.logger import get_logger
from web.auth import require_auth_api, require_perm

logger = get_logger("web.routes.export")

router = APIRouter(
    prefix="/api/export",
    tags=["Export"],
    # Nicht nur "angemeldet": hier gehen Audit-Trail und Spielerdaten raus.
    # Vorher konnte jedes Guild-Mitglied mit Dashboard-Zugang beides ziehen.
    dependencies=[Depends(require_perm("audit", "view"))],
)

# Unterstuetzte Exportzeitraeume (in Tagen)
ALLOWED_DAYS = {7, 30, 90}


# --- Hilfsfunktionen ---

# M33-Fix: CSV-Formula-Injection-Escaping
# Zeichen, die am Zellanfang eine Formel-Interpretation in
# Excel/LibreOffice ausloesen koennen (CWE-1236).
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_cell(value):
    """
    Neutralisiert CSV-Formula-Injection in einer einzelnen Zelle.

    Beginnt ein String-Wert mit einem gefaehrlichen Zeichen
    (=, +, -, @, Tab, CR), wird ein fuehrender Apostroph
    vorangestellt, damit Tabellenkalkulationen den Wert als
    Text statt als Formel behandeln. Nicht-Strings (Zahlen,
    None) bleiben unveraendert.

    Args:
        value: Roher Zellwert aus der Datenbank

    Returns:
        Neutralisierter Wert (String) oder das Original
    """
    if isinstance(value, str) and value.startswith(_CSV_INJECTION_PREFIXES):
        return "'" + value
    return value


def _rows_to_csv(headers: list[str], rows: list) -> str:
    """
    Wandelt Datenbankzeilen in einen CSV-String um.

    Verwendet den Standard-CSV-Writer mit Komma-Trennung.
    Die erste Zeile enthaelt die Spaltenkoepfe.

    Args:
        headers: Liste der Spaltenkoepfe
        rows: Liste der Datenzeilen (Tupel oder Row-Objekte)

    Returns:
        Fertiger CSV-String mit Header und Datenzeilen
    """
    output = io.StringIO()
    writer = csv.writer(output)
    # M33-Fix: CSV-Formula-Injection-Escaping auf alle Zellen anwenden
    writer.writerow([_sanitize_cell(h) for h in headers])
    for row in rows:
        writer.writerow([_sanitize_cell(cell) for cell in row])
    return output.getvalue()


def _make_filename(table_name: str) -> str:
    """
    Erzeugt den Dateinamen fuer den CSV-Export.

    Format: {table}_{YYYYMMDD}.csv

    Args:
        table_name: Name der exportierten Tabelle

    Returns:
        Dateiname mit aktuellem Datum
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{table_name}_{today}.csv"


def _csv_response(csv_content: str, table_name: str) -> StreamingResponse:
    """
    Erstellt eine StreamingResponse mit CSV-Inhalt und Download-Header.

    Args:
        csv_content: Der CSV-String
        table_name: Tabellenname fuer den Dateinamen

    Returns:
        StreamingResponse mit text/csv Content-Type
    """
    filename = _make_filename(table_name)
    return StreamingResponse(
        content=iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def _validate_days(days: int) -> int:
    """
    Validiert den days-Parameter. Faellt auf 30 zurueck wenn ungueltig.

    Args:
        days: Angeforderter Zeitraum in Tagen

    Returns:
        Validierter Zeitraum (7, 30 oder 90)
    """
    if days not in ALLOWED_DAYS:
        return 30
    return days


# --- Export-Endpunkte ---

@router.get("/player-sessions")
async def export_player_sessions(
    days: int = Query(default=30, description="Zeitraum in Tagen (7, 30, 90)"),
    current_user: dict = Depends(require_auth_api),
):
    """
    Exportiert Spieler-Sessions als CSV.

    Enthaelt: Spielername, Server-ID, Beitrittszeit, Austrittszeit,
    Dauer in Minuten.

    Query-Parameter:
        days: Zeitraum zurueck in Tagen (Standard: 30)
    """
    days = _validate_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT p.name, ps.server_type, ps.join_time, ps.leave_time, "
            "COALESCE(ps.duration_seconds / 60, 0) "
            "FROM player_sessions ps "
            "JOIN players p ON p.id = ps.player_id "
            "WHERE ps.join_time >= ? ORDER BY ps.join_time DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Fehler beim Exportieren der Spieler-Sessions: {e}")
        return JSONResponse(status_code=500, content={"error": "Datenbankfehler"})

    headers = ["Spielername", "Server-ID", "Beitritt", "Austritt", "Dauer (Minuten)"]
    csv_content = _rows_to_csv(headers, rows)

    logger.info(
        f"CSV-Export: player_sessions ({len(rows)} Zeilen, {days} Tage) "
        f"von {current_user.get('username', 'Unbekannt')}"
    )
    return _csv_response(csv_content, "player_sessions")


@router.get("/events")
async def export_events(
    days: int = Query(default=30, description="Zeitraum in Tagen (7, 30, 90)"),
    current_user: dict = Depends(require_auth_api),
):
    """
    Exportiert das Ereignis-Log als CSV.

    Enthaelt: Zeitstempel, Ereignistyp, Kategorie, Server-ID,
    Nachricht, Details.

    Query-Parameter:
        days: Zeitraum zurueck in Tagen (Standard: 30)
    """
    days = _validate_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT timestamp, event_type, category, server_id, message, details "
            "FROM events WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Fehler beim Exportieren der Events: {e}")
        return JSONResponse(status_code=500, content={"error": "Datenbankfehler"})

    headers = ["Zeitstempel", "Typ", "Kategorie", "Server-ID", "Nachricht", "Details"]
    csv_content = _rows_to_csv(headers, rows)

    logger.info(
        f"CSV-Export: events ({len(rows)} Zeilen, {days} Tage) "
        f"von {current_user.get('username', 'Unbekannt')}"
    )
    return _csv_response(csv_content, "events")


@router.get("/stats-history")
async def export_stats_history(
    days: int = Query(default=30, description="Zeitraum in Tagen (7, 30, 90)"),
    current_user: dict = Depends(require_auth_api),
):
    """
    Exportiert die System-Stats-Historie als CSV.

    Enthaelt: Zeitstempel, CPU-Auslastung, RAM-Auslastung,
    Festplatten-Auslastung (jeweils in Prozent).

    Query-Parameter:
        days: Zeitraum zurueck in Tagen (Standard: 30)
    """
    days = _validate_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT timestamp, cpu_percent, ram_percent, disk_percent "
            "FROM stats_history WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Fehler beim Exportieren der Stats-Historie: {e}")
        return JSONResponse(status_code=500, content={"error": "Datenbankfehler"})

    headers = ["Zeitstempel", "CPU (%)", "RAM (%)", "Disk (%)"]
    csv_content = _rows_to_csv(headers, rows)

    logger.info(
        f"CSV-Export: stats_history ({len(rows)} Zeilen, {days} Tage) "
        f"von {current_user.get('username', 'Unbekannt')}"
    )
    return _csv_response(csv_content, "stats_history")


@router.get("/audit-log")
async def export_audit_log(
    days: int = Query(default=30, description="Zeitraum in Tagen (7, 30, 90)"),
    current_user: dict = Depends(require_auth_api),
):
    """
    Exportiert das Audit-Log als CSV.

    Enthaelt: Zeitstempel, Aktion, Benutzer-ID, Benutzername,
    Ziel, Details.

    Query-Parameter:
        days: Zeitraum zurueck in Tagen (Standard: 30)
    """
    days = _validate_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT timestamp, action, user_id, username, target, details "
            "FROM audit_log WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Fehler beim Exportieren des Audit-Logs: {e}")
        return JSONResponse(status_code=500, content={"error": "Datenbankfehler"})

    headers = ["Zeitstempel", "Aktion", "Benutzer-ID", "Benutzername", "Ziel", "Details"]
    csv_content = _rows_to_csv(headers, rows)

    logger.info(
        f"CSV-Export: audit_log ({len(rows)} Zeilen, {days} Tage) "
        f"von {current_user.get('username', 'Unbekannt')}"
    )
    return _csv_response(csv_content, "audit_log")


@router.get("/command-log")
async def export_command_log(
    days: int = Query(default=30, description="Zeitraum in Tagen (7, 30, 90)"),
    current_user: dict = Depends(require_auth_api),
):
    """
    Exportiert das Command-Log als CSV.

    Enthaelt: Zeitstempel, Befehlsname, Benutzer-ID, Benutzername,
    Server-ID, Erfolgreich (ja/nein).

    Query-Parameter:
        days: Zeitraum zurueck in Tagen (Standard: 30)
    """
    days = _validate_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT timestamp, command_name, user_id, user_name, guild_id, success "
            "FROM command_log WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"Fehler beim Exportieren des Command-Logs: {e}")
        return JSONResponse(status_code=500, content={"error": "Datenbankfehler"})

    headers = ["Zeitstempel", "Befehl", "Benutzer-ID", "Benutzername", "Guild-ID", "Erfolgreich"]
    csv_content = _rows_to_csv(headers, rows)

    logger.info(
        f"CSV-Export: command_log ({len(rows)} Zeilen, {days} Tage) "
        f"von {current_user.get('username', 'Unbekannt')}"
    )
    return _csv_response(csv_content, "command_log")
