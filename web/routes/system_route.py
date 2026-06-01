"""
Phase 13i: System / Webmin — Service-Verwaltung und System-Info

Zeigt System-Informationen, Service-Status und einen Link
zum Webmin-Interface an. Ermöglicht Start/Stop/Restart von Services.
"""

import asyncio
import html
import json
import platform
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.config import get_env, MONITOR_DATA_DIR
from utils.logger import get_logger
from web.auth import require_auth, require_auth_api
from modules.database.db_manager import get_db

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

# Erlaubte Services für Start/Stop/Restart
ALLOWED_SERVICES = {svc["service_name"] for svc in KNOWN_SERVICES}


async def _is_service_active(service_name: str) -> bool:
    """Prüft ob ein systemd-Service aktiv ist."""
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
    """Sammelt den Status aller bekannten Services parallel.

    Vorher: sequentiell await je 1× systemctl-Subprocess pro Service
    (7 Services × 50-200ms = 0.35-1.4s).
    Jetzt: alle Subprocesses parallel via asyncio.gather (~50-200ms total).
    """
    # Parallele Status-Abfrage statt sequentielle Schleife
    active_results = await asyncio.gather(
        *(_is_service_active(svc["service_name"]) for svc in KNOWN_SERVICES),
        return_exceptions=True,
    )
    services = []
    for svc, active in zip(KNOWN_SERVICES, active_results):
        if isinstance(active, Exception):
            active = False
        services.append({
            "service_name": svc["service_name"],
            "display_name": svc["display_name"],
            "active": active,
        })
    return services


def _get_system_info() -> dict:
    """Sammelt grundlegende System-Informationen via psutil (falls verfügbar)."""
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
        # interval=None: cached delta (kein Block). Erster Call = 0.0, danach real.
        info["cpu_percent"] = psutil.cpu_percent(interval=None)

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
        logger.debug("psutil nicht verfügbar — System-Info eingeschränkt")
    except Exception as e:
        logger.warning(f"Fehler beim Sammeln der System-Info: {e}")

    return info


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, current_user: dict = Depends(require_auth)):
    """
    Zeigt die System-Verwaltungsseite mit System-Info, Service-Status
    und einem Link zum Webmin-Interface.
    """
    # _get_system_info ist sync (psutil) → in Thread auslagern, parallel zu Service-Status.
    system_info, services = await asyncio.gather(
        asyncio.to_thread(_get_system_info),
        _collect_service_status(),
    )

    return templates.TemplateResponse("system.html", {
        "request": request,
        "user": current_user,
        "webmin_url": WEBMIN_URL,
        "system_info": system_info,
        "services": services,
    })


_service_action_locks: dict[str, asyncio.Lock] = {}


def _get_service_action_lock(service_name: str) -> asyncio.Lock:
    """Per-Service-Lock fuer service_action — verhindert konkurrierende
    start/stop/restart-Calls (z. B. zwei Browser-Tabs gleichzeitig).
    """
    if service_name not in _service_action_locks:
        _service_action_locks[service_name] = asyncio.Lock()
    return _service_action_locks[service_name]


@router.post("/api/system/service/action")
async def service_action(request: Request, current_user: dict = Depends(require_auth_api)):
    """
    Führt eine Service-Aktion aus (start/stop/restart).
    Nur für authentifizierte Benutzer.
    """
    user = current_user
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
                f'<div class="alert alert-danger">Ungültige Aktion: {html.escape(action)}</div>'
            )

        # Service-Aktion ausführen — Per-Service-Lock verhindert konkurrierende Calls
        # (z. B. zwei Browser-Tabs gleichzeitig start+stop). Lock umfasst den
        # gesamten Subprocess-Lifecycle bis communicate() done ist.
        lock = _get_service_action_lock(service_name)
        async with lock:
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
            username = user.get("username", "Unbekannt")
            logger.info(f"Service {service_name} {label} von {username}")

            # F35: Audit-Log-Eintrag für Service-Aktionen
            try:
                from datetime import datetime, timezone
                db = await get_db()
                await db.execute(
                    "INSERT INTO audit_log (timestamp, action, user_id, user_name, target, details, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        f"service_{action}",
                        user.get("id", ""),
                        username,
                        service_name,
                        f"Service {service_name} {label}",
                        "dashboard",
                    ),
                )
                await db.commit()
            except Exception as audit_err:
                logger.warning(f"Audit-Log fehlgeschlagen: {audit_err}")

            return HTMLResponse(
                f'<div class="alert alert-success">{html.escape(service_name)} erfolgreich {label}.</div>'
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
#  Package-Updates (apt)
# ==============================================================


async def _get_upgradable_packages() -> list[dict]:
    """Liest verfuegbare Package-Updates via apt list --upgradable."""
    packages = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "apt", "list", "--upgradable",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        output = stdout.decode("utf-8", errors="replace")

        for line in output.strip().split("\n"):
            # Format: "paketname/suite version arch [upgradable from: alte_version]"
            if "[upgradable from:" in line:
                parts = line.split("/", 1)
                if len(parts) >= 2:
                    pkg_name = parts[0].strip()
                    rest = parts[1]
                    # Neue Version extrahieren
                    version_parts = rest.split(" ")
                    new_version = version_parts[1] if len(version_parts) > 1 else "?"
                    # Alte Version extrahieren
                    old_version = "?"
                    if "from:" in rest:
                        old_version = rest.split("from:")[-1].strip().rstrip("]").strip()
                    packages.append({
                        "name": pkg_name,
                        "current": old_version,
                        "available": new_version,
                    })
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning(f"Fehler beim Lesen der Package-Updates: {e}")

    return packages


@router.get("/api/system/packages/list", response_class=HTMLResponse)
async def get_package_list(current_user: dict = Depends(require_auth_api)):
    """Gibt die Liste verfuegbarer Updates als HTML-Partial zurueck."""
    packages = await _get_upgradable_packages()

    if not packages:
        return HTMLResponse(
            '<p style="color: var(--success); font-weight: 600;">Alle Pakete sind aktuell.</p>'
        )

    rows = ""
    for pkg in packages:
        rows += f"""<tr>
            <td style="font-weight: 500;">{html.escape(pkg['name'])}</td>
            <td style="color: var(--text-muted);">{html.escape(pkg['current'])}</td>
            <td style="color: var(--warning); font-weight: 600;">{html.escape(pkg['available'])}</td>
        </tr>"""

    result_html = f"""
    <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 0.75rem;">
        {len(packages)} Update{'s' if len(packages) != 1 else ''} verfuegbar
    </p>
    <div class="table-wrapper">
        <table class="data-table">
            <thead><tr><th>Paket</th><th>Installiert</th><th>Verfuegbar</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(result_html)


@router.post("/api/system/packages/check", response_class=HTMLResponse)
async def check_package_updates(current_user: dict = Depends(require_auth_api)):
    """Fuehrt apt update aus und gibt dann die Update-Liste zurueck."""
    logger.info(f"Package-Update-Check von {current_user.get('username', 'Unbekannt')}")

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "apt", "update", "-qq",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except (asyncio.TimeoutError, OSError) as e:
        return HTMLResponse(f'<div class="alert alert-danger">apt update fehlgeschlagen: {html.escape(str(e)[:200])}</div>')

    # Jetzt aktualisierte Liste abrufen
    packages = await _get_upgradable_packages()

    if not packages:
        return HTMLResponse(
            '<p style="color: var(--success); font-weight: 600;">Alle Pakete sind aktuell.</p>'
        )

    rows = ""
    for pkg in packages:
        rows += f"""<tr>
            <td style="font-weight: 500;">{html.escape(pkg['name'])}</td>
            <td style="color: var(--text-muted);">{html.escape(pkg['current'])}</td>
            <td style="color: var(--warning); font-weight: 600;">{html.escape(pkg['available'])}</td>
        </tr>"""

    result_html = f"""
    <div class="alert alert-success" style="margin-bottom: 0.75rem;">Paketlisten aktualisiert.</div>
    <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 0.75rem;">
        {len(packages)} Update{'s' if len(packages) != 1 else ''} verfuegbar
    </p>
    <div class="table-wrapper">
        <table class="data-table">
            <thead><tr><th>Paket</th><th>Installiert</th><th>Verfuegbar</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(result_html)


@router.post("/api/system/packages/upgrade", response_class=HTMLResponse)
async def upgrade_packages(current_user: dict = Depends(require_auth_api)):
    """Fuehrt apt upgrade -y aus und aktualisiert alle Pakete."""
    logger.info(f"Package-Upgrade gestartet von {current_user.get('username', 'Unbekannt')}")

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "apt", "upgrade", "-y", "-qq",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)

        if proc.returncode == 0:
            output = stdout.decode("utf-8", errors="replace").strip()
            # Anzahl aktualisierter Pakete zaehlen
            upgraded_lines = [l for l in output.split("\n") if l.strip()]
            count = len(upgraded_lines) if upgraded_lines and upgraded_lines[0] else 0

            logger.info(f"Package-Upgrade abgeschlossen: {count} Pakete")
            return HTMLResponse(
                f'<div class="alert alert-success">System-Update erfolgreich abgeschlossen.</div>'
            )
        else:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"Package-Upgrade Fehler: {error_msg}")
            return HTMLResponse(
                f'<div class="alert alert-danger">Fehler beim Update: {html.escape(error_msg[:300])}</div>'
            )

    except asyncio.TimeoutError:
        return HTMLResponse(
            '<div class="alert alert-danger">Timeout — Update dauert zu lange (10 Min. Limit).</div>'
        )
    except Exception as e:
        logger.error(f"Package-Upgrade Exception: {e}")
        return HTMLResponse(
            f'<div class="alert alert-danger">Fehler: {html.escape(str(e)[:200])}</div>'
        )


# ==============================================================
#  F42: Package-Checker Status (SQLite-basiert)
# ==============================================================


def _read_json_file(filepath: Path) -> dict | None:
    """Liest eine JSON-Datei sicher ein."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"JSON-Datei nicht lesbar ({filepath.name}): {e}")
    return None


@router.get("/api/system/packages/status")
async def package_checker_status(current_user: dict = Depends(require_auth_api)):
    """F42: Gibt den letzten Package-Check-Status zurueck (aus JSON-Bridge)."""
    data = _read_json_file(MONITOR_DATA_DIR / "package_checker.json")
    if data is None:
        return JSONResponse(content={"status": "unknown", "message": "Noch kein Check"})
    return JSONResponse(content=data)


@router.get("/api/system/packages/history")
async def package_checker_history(current_user: dict = Depends(require_auth_api)):
    """F42: Gibt die Historie der Package-Checks aus SQLite zurueck."""
    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, timestamp, message, details FROM events "
            "WHERE event_type = 'package_check' "
            "ORDER BY id DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        history = []
        for row in rows:
            entry = {
                "id": row["id"],
                "timestamp": str(row["timestamp"]) if row["timestamp"] else "",
                "message": row["message"] or "",
            }
            if row["details"]:
                try:
                    entry["details"] = json.loads(row["details"])
                except (json.JSONDecodeError, TypeError):
                    entry["details"] = row["details"]
            history.append(entry)
        return JSONResponse(content={"history": history})
    except Exception as e:
        logger.error(f"Package-History Fehler: {e}")
        return JSONResponse(content={"history": [], "error": str(e)})
