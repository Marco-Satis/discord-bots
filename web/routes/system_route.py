"""
Phase 13i: System / Webmin — Service-Verwaltung und System-Info

Zeigt System-Informationen, Service-Status und einen Link
zum Webmin-Interface an. Ermoeglicht Start/Stop/Restart von Services.
"""

import asyncio
import html
import platform
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.config import get_env, PROJECT_ROOT
from utils.logger import get_logger
from web.auth import get_current_user

logger = get_logger("web.routes.system")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["System"])

# Webmin-URL konfigurierbar (Standard: Webmin auf Port 9090)
WEBMIN_URL = get_env("WEB_WEBMIN_URL", "https://203.0.113.10:9090")

# Bekannte Services mit Anzeigenamen
KNOWN_SERVICES = [
    {"service_name": "gameserver-bot.service", "display_name": "GameServer Bot"},
    {"service_name": "monitor-bot.service", "display_name": "Monitor Bot"},
    {"service_name": "admin-bot.service", "display_name": "Admin Bot"},
    {"service_name": "web-dashboard.service", "display_name": "Web Dashboard"},
    {"service_name": "satisfactory.service", "display_name": "Satisfactory Server"},
    {"service_name": "minecraft-bmc.service", "display_name": "MC Better MC"},
    {"service_name": "minecraft-vanilla.service", "display_name": "MC Vanilla"},
]

# Erlaubte Services fuer Start/Stop/Restart
ALLOWED_SERVICES = {svc["service_name"] for svc in KNOWN_SERVICES}


async def _is_service_active(service_name: str) -> bool:
    """Prueft ob ein systemd-Service aktiv ist."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "--quiet", service_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        return proc.returncode == 0
    except (asyncio.TimeoutError, OSError, Exception):
        return False


async def _collect_service_status() -> list[dict]:
    """Sammelt den Status aller bekannten Services."""
    services = []
    for svc in KNOWN_SERVICES:
        active = await _is_service_active(svc["service_name"])
        services.append({
            "service_name": svc["service_name"],
            "display_name": svc["display_name"],
            "active": active,
        })
    return services


def _get_system_info() -> dict:
    """Sammelt grundlegende System-Informationen via psutil (falls verfuegbar)."""
    info = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_count": 0,
        "cpu_percent": 0.0,
        "ram_total_gb": 0.0,
        "ram_used_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_used_percent": 0.0,
        "uptime": "Unbekannt",
    }
    try:
        import psutil
        import time

        info["cpu_count"] = psutil.cpu_count(logical=True) or 0
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)

        mem = psutil.virtual_memory()
        info["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
        info["ram_used_percent"] = mem.percent

        disk = psutil.disk_usage("/")
        info["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
        info["disk_used_percent"] = disk.percent

        boot_time = psutil.boot_time()
        uptime_sec = int(time.time() - boot_time)
        days, remainder = divmod(uptime_sec, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        info["uptime"] = f"{days}d {hours}h {minutes}m"
    except ImportError:
        logger.debug("psutil nicht verfuegbar — System-Info eingeschraenkt")
    except Exception as e:
        logger.warning(f"Fehler beim Sammeln der System-Info: {e}")

    return info


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    """
    Zeigt die System-Verwaltungsseite mit System-Info, Service-Status
    und einem Link zum Webmin-Interface.
    """
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    system_info = _get_system_info()
    services = await _collect_service_status()

    return templates.TemplateResponse("system.html", {
        "request": request,
        "user": user,
        "webmin_url": WEBMIN_URL,
        "system_info": system_info,
        "services": services,
    })


@router.post("/api/system/service/action")
async def service_action(request: Request):
    """
    Fuehrt eine Service-Aktion aus (start/stop/restart).
    Nur fuer authentifizierte Benutzer.
    """
    user = get_current_user(request)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Nicht authentifiziert"}
        )

    try:
        form = await request.form()
        service_name = form.get("service", "")
        action = form.get("action", "")

        # Validierung
        if service_name not in ALLOWED_SERVICES:
            return HTMLResponse(
                f'<div class="alert alert-danger">Unbekannter Service: {html.escape(service_name)}</div>'
            )

        if action not in ("start", "stop", "restart"):
            return HTMLResponse(
                f'<div class="alert alert-danger">Ungueltige Aktion: {html.escape(action)}</div>'
            )

        # Service-Aktion ausfuehren
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", action, service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=30.0
        )

        if proc.returncode == 0:
            action_labels = {"start": "gestartet", "stop": "gestoppt", "restart": "neugestartet"}
            label = action_labels.get(action, action)
            logger.info(f"Service {service_name} {label} von {user.get('username', 'Unbekannt')}")
            return HTMLResponse(
                f'<div class="alert alert-success">{service_name} erfolgreich {label}.</div>'
            )
        else:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"Service-Aktion fehlgeschlagen: {service_name} {action} — {error_msg}")
            return HTMLResponse(
                f'<div class="alert alert-danger">Fehler: {html.escape(error_msg[:200])}</div>'
            )

    except asyncio.TimeoutError:
        return HTMLResponse(
            '<div class="alert alert-danger">Timeout — Service reagiert nicht.</div>'
        )
    except Exception as e:
        logger.error(f"Service-Aktion Fehler: {e}")
        return HTMLResponse(
            f'<div class="alert alert-danger">Fehler: {html.escape(str(e)[:200])}</div>'
        )


# ==============================================================
#  Log-Verwaltung
# ==============================================================

LOGS_DIR = PROJECT_ROOT / "logs"

# Erlaubte Log-Dateien (Sicherheit: nur bekannte Dateien loeschbar)
ALLOWED_LOG_PATTERNS = ["*.log", "*.log.*"]


def _collect_log_files() -> list[dict]:
    """Sammelt alle Log-Dateien mit Groesse und Aenderungsdatum."""
    log_files = []
    if not LOGS_DIR.exists():
        return log_files

    for pattern in ALLOWED_LOG_PATTERNS:
        for log_file in sorted(LOGS_DIR.glob(pattern)):
            try:
                stat = log_file.stat()
                size_kb = round(stat.st_size / 1024, 1)
                size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 1)} MB"
                log_files.append({
                    "name": log_file.name,
                    "size": size_str,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                })
            except OSError:
                pass

    # Nach Groesse sortieren (groesste zuerst)
    log_files.sort(key=lambda x: x["size_bytes"], reverse=True)
    return log_files


@router.get("/api/system/logs", response_class=HTMLResponse)
async def get_logs_list(request: Request):
    """Gibt die Log-Datei-Liste als HTML-Partial zurueck."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse('<div class="alert alert-danger">Nicht authentifiziert</div>', status_code=401)

    log_files = _collect_log_files()
    total_size = sum(f["size_bytes"] for f in log_files)
    total_str = f"{round(total_size / 1024, 1)} KB" if total_size < 1048576 else f"{round(total_size / 1048576, 1)} MB"

    rows = ""
    for lf in log_files:
        rows += f"""<tr>
            <td style="font-weight: 500;">{lf['name']}</td>
            <td>{lf['size']}</td>
            <td>
                <button class="btn btn-sm btn-danger"
                        hx-post="/api/system/logs/delete"
                        hx-vals='{{"filename": "{lf['name']}"}}'
                        hx-target="#log-management-area"
                        hx-swap="innerHTML"
                        hx-confirm="Log-Datei {lf['name']} loeschen?">
                    Loeschen
                </button>
            </td>
        </tr>"""

    html = f"""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
        <span style="color: var(--text-secondary); font-size: 0.85rem;">
            {len(log_files)} Log-Dateien — Gesamt: {total_str}
        </span>
        <button class="btn btn-sm btn-danger"
                hx-post="/api/system/logs/delete-all"
                hx-target="#log-management-area"
                hx-swap="innerHTML"
                hx-confirm="ALLE Log-Dateien loeschen? Diese Aktion kann nicht rueckgaengig gemacht werden.">
            Alle Logs loeschen
        </button>
    </div>
    <div class="table-wrapper">
        <table class="data-table">
            <thead><tr><th>Datei</th><th>Groesse</th><th>Aktion</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="3" style="text-align: center; color: var(--text-muted);">Keine Log-Dateien vorhanden.</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(html)


@router.post("/api/system/logs/delete")
async def delete_log_file(request: Request):
    """Loescht eine einzelne Log-Datei."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse('<div class="alert alert-danger">Nicht authentifiziert</div>', status_code=401)

    form = await request.form()
    filename = form.get("filename", "").strip()

    # Sicherheitspruefung: Nur Dateien im logs-Verzeichnis
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return HTMLResponse('<div class="alert alert-danger">Ungueltiger Dateiname.</div>')

    filepath = LOGS_DIR / filename
    if not filepath.exists():
        return HTMLResponse(f'<div class="alert alert-warning">{html.escape(filename)} existiert nicht.</div>')

    # Sicherheit: Datei muss im logs-Verzeichnis liegen
    try:
        filepath.resolve().relative_to(LOGS_DIR.resolve())
    except ValueError:
        return HTMLResponse('<div class="alert alert-danger">Zugriff verweigert.</div>')

    try:
        filepath.unlink()
        logger.info(f"Log-Datei geloescht: {filename} (von {user.get('username', 'Unbekannt')})")
    except OSError as e:
        return HTMLResponse(f'<div class="alert alert-danger">Fehler beim Loeschen von Log-Datei.</div>')

    # Aktualisierte Liste zurueckgeben
    return await get_logs_list(request)


@router.post("/api/system/logs/delete-all")
async def delete_all_logs(request: Request):
    """Loescht alle Log-Dateien."""
    user = get_current_user(request)
    if user is None:
        return HTMLResponse('<div class="alert alert-danger">Nicht authentifiziert</div>', status_code=401)

    deleted = 0
    errors = 0

    if LOGS_DIR.exists():
        for pattern in ALLOWED_LOG_PATTERNS:
            for log_file in LOGS_DIR.glob(pattern):
                try:
                    # Sicherheit: Nur Dateien im logs-Verzeichnis
                    log_file.resolve().relative_to(LOGS_DIR.resolve())
                    log_file.unlink()
                    deleted += 1
                except (OSError, ValueError):
                    errors += 1

    logger.info(f"Alle Logs geloescht: {deleted} Dateien (von {user.get('username', 'Unbekannt')})")

    msg = f'<div class="alert alert-success">{deleted} Log-Dateien geloescht.</div>'
    if errors:
        msg += f'<div class="alert alert-warning">{errors} Dateien konnten nicht geloescht werden.</div>'

    # Aktualisierte (leere) Liste anhaengen
    return HTMLResponse(msg + await _get_logs_html(request))


async def _get_logs_html(request: Request) -> str:
    """Hilfsfunktion: Gibt die Log-Liste als HTML-String zurueck."""
    response = await get_logs_list(request)
    return response.body.decode("utf-8")
