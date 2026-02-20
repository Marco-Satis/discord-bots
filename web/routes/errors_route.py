"""
Phase 13a: Fehler-Uebersicht — Anzeige von ERROR/WARNING aus Log-Dateien

Liest die letzten Fehler und Warnungen aus den Bot-Log-Dateien
und stellt sie in einer filterbaren Tabelle dar.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.config import LOG_DIR
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.errors")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Fehler"])

# Log-Zeilen-Pattern: "2025-01-15 12:00:00 [bot_name] ERROR: Nachricht"
LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"\[([^\]]+)\]\s+"
    r"(ERROR|WARNING):\s+"
    r"(.+)$"
)

# Maximale Anzahl Zeilen pro Datei die gescannt werden
MAX_LINES_PER_FILE = 5000
# Maximale Anzahl Ergebnisse die angezeigt werden
MAX_RESULTS = 200


def _scan_log_file(log_path: Path) -> list[dict]:
    """
    Scannt eine einzelne Log-Datei nach ERROR- und WARNING-Zeilen.
    Liest die letzten MAX_LINES_PER_FILE Zeilen.
    """
    entries = []
    try:
        if not log_path.exists() or log_path.stat().st_size == 0:
            return entries

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Letzte Zeilen lesen (effizient fuer grosse Dateien)
            lines = f.readlines()
            recent_lines = lines[-MAX_LINES_PER_FILE:]

        for line in recent_lines:
            line = line.strip()
            match = LOG_PATTERN.match(line)
            if match:
                timestamp_str, bot_name, level, message = match.groups()
                entries.append({
                    "timestamp": timestamp_str,
                    "bot": bot_name,
                    "level": level,
                    "message": message[:500],  # Nachricht auf 500 Zeichen begrenzen
                    "source_file": log_path.name,
                })
    except (IOError, UnicodeDecodeError) as e:
        logger.warning(f"Fehler beim Lesen von {log_path}: {e}")

    return entries


def _collect_all_errors(level_filter: str = "") -> list[dict]:
    """
    Sammelt alle ERROR/WARNING-Eintraege aus allen Log-Dateien.

    Args:
        level_filter: Optional "ERROR" oder "WARNING" zum Filtern.
    """
    all_entries = []

    if not LOG_DIR.exists():
        logger.debug(f"Log-Verzeichnis nicht gefunden: {LOG_DIR}")
        return all_entries

    # Alle .log-Dateien durchsuchen
    for log_file in sorted(LOG_DIR.glob("*.log")):
        entries = _scan_log_file(log_file)
        all_entries.extend(entries)

    # Nach Level filtern falls gewuenscht
    if level_filter in ("ERROR", "WARNING"):
        all_entries = [e for e in all_entries if e["level"] == level_filter]

    # Nach Zeitstempel sortieren (neueste zuerst)
    all_entries.sort(key=lambda e: e["timestamp"], reverse=True)

    # Auf Maximum begrenzen
    return all_entries[:MAX_RESULTS]


@router.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request, level: str = ""):
    """
    Fehler-Uebersicht mit optionalem Level-Filter.
    Unterstuetzt HTMX-Fragment-Anfragen fuer Auto-Refresh.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    entries = _collect_all_errors(level_filter=level.upper() if level else "")

    # Pruefen ob es ein HTMX-Request ist (nur Tabellen-Fragment zurueckgeben)
    is_htmx = request.headers.get("HX-Request") == "true"

    return templates.TemplateResponse("errors.html", {
        "request": request,
        "user": user,
        "entries": entries,
        "active_filter": level.upper() if level else "ALL",
        "is_htmx": is_htmx,
    })
