"""
Phase 13i: System / Webmin — Service-Verwaltung und System-Info

Zeigt System-Informationen, Service-Status und einen Link
zum Webmin-Interface an. Ermöglicht Start/Stop/Restart von Services.
"""

import asyncio
import html
import json
import platform
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.config import get_env, MONITOR_DATA_DIR
from utils.logger import get_logger
from web.auth import require_auth, require_auth_api, require_perm
from modules.database.db_manager import get_db

logger = get_logger("web.routes.system")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["System"])

# Webmin-URL konfigurierbar (Standard: Webmin auf Port 9090)
WEBMIN_URL = get_env("WEB_WEBMIN_URL", "https://203.0.113.10:9090")

# Bekannte Services mit Anzeigenamen
KNOWN_SERVICES = [
    {"service_name": "operator-bot.service", "display_name": "Operator"},
    {"service_name": "recon-bot.service", "display_name": "Recon"},
    {"service_name": "marshal-bot.service", "display_name": "Marshal"},
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
async def service_action(request: Request, current_user: dict = Depends(require_perm("system", "control"))):
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


# Regex fuer apt-get-Simulations-Output:
#   Inst <pkg> [<old-version>] (<new-version> <repo...> [<arch>]) [...]
#   Inst <pkg> (<new-version> <repo...>)          (Neu-Installation, kein [old])
_INST_RE = re.compile(
    r"^Inst\s+(?P<name>\S+)\s+"
    r"(?:\[(?P<old>[^\]]+)\]\s+)?"
    r"\((?P<new>\S+)\s"
)


# Zeilen die im apt-Output nur Rauschen sind (kein echter Fehler).
_APT_NOISE = (
    "WARNING: apt does not have a stable CLI",
    "debconf:",
    "dialog frontend",
    "Dialog frontend will not work",
    "falling back to frontend",
    "unable to initialize frontend",
    "(Reading database",
    "Preparing to unpack",
    "Unpacking ",
    "Selecting previously",
)


def _clean_apt_output(text: str) -> str:
    """Entfernt Rausch-Zeilen (debconf/CLI-Warnings) aus apt-Output fuer saubere Anzeige."""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(noise.lower() in s.lower() for noise in _APT_NOISE):
            continue
        lines.append(s)
    return "\n".join(lines)


def _parse_apt_sim(output: str) -> list[dict]:
    """Parst `apt-get -s {upgrade|dist-upgrade}`-Output (Inst-Zeilen)."""
    packages: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Inst "):
            continue
        m = _INST_RE.match(line)
        if not m:
            continue
        packages.append({
            "name": m.group("name"),
            "current": m.group("old") or "(neu)",
            "available": m.group("new"),
        })
    return packages


async def _apt_sim(mode: str) -> str:
    """Fuehrt `apt-get -s <mode>` aus (Simulation, read-only, kein sudo noetig).

    LANG=C erzwingt parsbaren englischen Output (sonst Server-Locale).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "-s", mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return stdout.decode("utf-8", errors="replace")
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning(f"apt-get -s {mode} fehlgeschlagen: {e}")
        return ""


async def _get_upgradable_packages() -> list[dict]:
    """Pakete die `apt upgrade` installieren wuerde.

    Nutzt `apt-get -s upgrade` (Simulation) statt `apt list --upgradable` —
    letzteres versteckt Phased-Updates + zeigt Pakete die gar nicht installiert
    werden, was zu falschen Zaehlungen fuehrt. `-s upgrade` matcht exakt den
    Upgrade-Button (`apt upgrade -y`).
    """
    return _parse_apt_sim(await _apt_sim("upgrade"))


async def _get_heldback_packages() -> list[dict]:
    """Pakete die nur via `apt full-upgrade`/`dist-upgrade` kommen (held-back).

    Diff dist-upgrade − upgrade. Diese brauchen neue Dependencies und werden
    von `apt upgrade` zurueckgehalten. Erklaeren die Differenz zu Webmins Zaehlung.
    """
    upgrade_names = {p["name"] for p in await _get_upgradable_packages()}
    dist = _parse_apt_sim(await _apt_sim("dist-upgrade"))
    return [p for p in dist if p["name"] not in upgrade_names]


async def _heldback_html() -> str:
    """HTML-Sektion fuer held-back Pakete (nur via dist-upgrade) — sonst leer.

    Diese Pakete erscheinen NICHT in `apt upgrade` (Button), brauchen neue
    Dependencies. Erklaert die Differenz zu Webmins hoeherer Zaehlung.
    """
    held = await _get_heldback_packages()
    if not held:
        return ""
    rows = "".join(
        f"<tr><td style='font-weight:500'>{html.escape(p['name'])}</td>"
        f"<td style='color:var(--text-muted)'>{html.escape(p['current'])}</td>"
        f"<td style='color:var(--accent-light);font-weight:600'>{html.escape(p['available'])}</td></tr>"
        for p in held
    )
    return f"""
    <details style="margin-top:14px" open>
      <summary style="cursor:pointer;color:var(--t2);font-size:13px;font-weight:600">
        {len(held)} zurueckgehaltene{'s' if len(held) == 1 else ''} Update{'s' if len(held) != 1 else ''}
        (brauchen Full-Upgrade)
      </summary>
      <p style="color:var(--text-muted);font-size:11px;margin:8px 0">
        Diese Pakete erfordern neue Abhaengigkeiten und werden vom Standard-Update
        nicht installiert. Full-Upgrade installiert sie &mdash; kann dabei einzelne
        Pakete entfernen.
      </p>
      <div class="table-wrapper">
        <table class="data-table">
          <thead><tr><th>Paket</th><th>Installiert</th><th>Verfuegbar</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <button class="btn btn-sm btn-warning" style="margin-top:10px"
              hx-post="/api/system/packages/full-upgrade"
              hx-target="#package-update-result"
              hx-swap="innerHTML"
              hx-confirm="Full-Upgrade installiert {len(held)} zurueckgehaltene Paket(e) und kann dabei Pakete ENTFERNEN. Fortfahren?">
        Zurueckgehaltene installieren (Full-Upgrade)
      </button>
    </details>
    """


def _check_reboot_required() -> dict:
    """Prueft ob ein System-Reboot nach Updates noetig ist.

    Ubuntu legt /var/run/reboot-required an wenn Kernel/glibc/systemd-Update
    installiert wurde. /var/run/reboot-required.pkgs listet die ausloesenden
    Pakete. Liest beide Files read-only (kein sudo noetig).

    Returns: {"required": bool, "packages": list[str]}
    """
    flag = Path("/var/run/reboot-required")
    pkgs_file = Path("/var/run/reboot-required.pkgs")
    if not flag.exists():
        return {"required": False, "packages": []}
    pkgs: list[str] = []
    try:
        if pkgs_file.exists():
            pkgs = [
                line.strip() for line in pkgs_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    except OSError as e:
        logger.debug(f"reboot-required.pkgs nicht lesbar: {e}")
    return {"required": True, "packages": sorted(set(pkgs))}


def _reboot_banner_html() -> str:
    """Liefert HTML-Banner wenn Reboot noetig — sonst leerer String."""
    status = _check_reboot_required()
    if not status["required"]:
        return ""
    pkgs = status["packages"]
    pkg_list = (
        f' <span style="font-family:ui-monospace,monospace;font-size:11px;color:var(--t3)">'
        f'({html.escape(", ".join(pkgs[:6]))}{"…" if len(pkgs) > 6 else ""})</span>'
        if pkgs else ""
    )
    return (
        '<div class="alert alert-warning" style="margin-bottom:12px">'
        '<strong>System-Reboot empfohlen</strong> — '
        'Kernel oder Kern-Bibliothek aktualisiert, voller Effekt erst nach Neustart.'
        f'{pkg_list}'
        '</div>'
    )


@router.get("/api/system/packages/list", response_class=HTMLResponse)
async def get_package_list(current_user: dict = Depends(require_auth_api)):
    """Gibt die Liste verfuegbarer Updates als HTML-Partial zurueck."""
    packages = await _get_upgradable_packages()
    reboot_banner = _reboot_banner_html()
    heldback = await _heldback_html()

    if not packages:
        return HTMLResponse(
            f'{reboot_banner}'
            '<p style="color: var(--success); font-weight: 600;">Alle Pakete sind aktuell.</p>'
            f'{heldback}'
        )

    rows = ""
    for pkg in packages:
        rows += f"""<tr>
            <td style="font-weight: 500;">{html.escape(pkg['name'])}</td>
            <td style="color: var(--text-muted);">{html.escape(pkg['current'])}</td>
            <td style="color: var(--warning); font-weight: 600;">{html.escape(pkg['available'])}</td>
        </tr>"""

    result_html = f"""
    {reboot_banner}
    <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 0.75rem;">
        {len(packages)} Update{'s' if len(packages) != 1 else ''} verfuegbar
    </p>
    <div class="table-wrapper">
        <table class="data-table">
            <thead><tr><th>Paket</th><th>Installiert</th><th>Verfuegbar</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    {heldback}
    """
    return HTMLResponse(result_html)


@router.post("/api/system/packages/check", response_class=HTMLResponse)
async def check_package_updates(current_user: dict = Depends(require_perm("system", "control"))):
    """Fuehrt apt update aus und gibt dann die Update-Liste zurueck."""
    logger.info(f"Package-Update-Check von {current_user.get('username', 'Unbekannt')}")

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "/usr/bin/apt", "update",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except (asyncio.TimeoutError, OSError) as e:
        return HTMLResponse(f'<div class="alert alert-danger">apt update fehlgeschlagen: {html.escape(str(e)[:200])}</div>')

    # Jetzt aktualisierte Liste abrufen
    packages = await _get_upgradable_packages()
    reboot_banner = _reboot_banner_html()
    heldback = await _heldback_html()

    if not packages:
        return HTMLResponse(
            f'{reboot_banner}'
            '<div class="alert alert-success" style="margin-bottom: 0.75rem;">Paketlisten aktualisiert.</div>'
            '<p style="color: var(--success); font-weight: 600;">Alle Pakete sind aktuell.</p>'
            f'{heldback}'
        )

    rows = ""
    for pkg in packages:
        rows += f"""<tr>
            <td style="font-weight: 500;">{html.escape(pkg['name'])}</td>
            <td style="color: var(--text-muted);">{html.escape(pkg['current'])}</td>
            <td style="color: var(--warning); font-weight: 600;">{html.escape(pkg['available'])}</td>
        </tr>"""

    result_html = f"""
    {reboot_banner}
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
    {heldback}
    """
    return HTMLResponse(result_html)


async def _run_apt_upgrade(wrapper: str, label: str) -> HTMLResponse:
    """Fuehrt ein Root-Wrapper-Upgrade-Script aus + rendert Ergebnis.

    Shared von /packages/upgrade (apt upgrade) + /packages/full-upgrade (dist-upgrade).
    Beide Wrapper sind root-owned, fixe Command, keine User-Args → injection-sicher.
    """
    # Vor-Snapshot fuer Diff (dist-upgrade-Snapshot deckt beide Faelle ab)
    pre_names = {p["name"] for p in await _get_upgradable_packages()}
    pre_names |= {p["name"] for p in await _get_heldback_packages()}

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", wrapper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)

        if proc.returncode == 0:
            post_names = {p["name"] for p in await _get_upgradable_packages()}
            post_names |= {p["name"] for p in await _get_heldback_packages()}
            installed = sorted(pre_names - post_names)
            count = len(installed)

            logger.info(f"{label} abgeschlossen: {count} Pakete ({', '.join(installed) if installed else 'keine'})")

            installed_html = ""
            if installed:
                rows = "".join(f"<li><code>{html.escape(n)}</code></li>" for n in installed)
                installed_html = (
                    f'<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--t2);font-size:12px">'
                    f'{count} Paket{"e" if count != 1 else ""} aktualisiert &mdash; Details</summary>'
                    f'<ul style="margin:8px 0 0 16px;font-family:ui-monospace,monospace;font-size:12px">{rows}</ul></details>'
                )
            still_open = (
                f'<p style="margin-top:8px;color:var(--warn);font-size:12px">{len(post_names)} Update(s) bleiben offen: {html.escape(", ".join(sorted(post_names)))}</p>'
                if post_names else ""
            )
            html_body = (
                f'{_reboot_banner_html()}'
                f'<div class="alert alert-success">{label} erfolgreich &mdash; '
                f'{count} Paket{"e" if count != 1 else ""} installiert.</div>'
                f'{installed_html}{still_open}'
            )
            return HTMLResponse(html_body, headers={"HX-Trigger": "packageListChanged"})

        error_msg = stderr.decode("utf-8", errors="replace")
        cleaned = _clean_apt_output(error_msg)
        logger.warning(f"{label} Fehler (rc={proc.returncode}): {error_msg.strip()[:500]}")
        pre = (
            f'<pre class="code-block" style="white-space:pre-wrap;word-break:break-word;'
            f'max-height:240px;overflow:auto">{html.escape(cleaned[:1500])}</pre>'
            if cleaned else ""
        )
        return HTMLResponse(
            f'<div class="alert alert-danger">{label} fehlgeschlagen '
            f'(Exit-Code {proc.returncode}).</div>{pre}'
        )

    except asyncio.TimeoutError:
        return HTMLResponse(
            f'<div class="alert alert-danger">Timeout — {label} dauert zu lange (10 Min. Limit).</div>'
        )
    except Exception as e:
        logger.error(f"{label} Exception: {e}")
        return HTMLResponse(
            f'<div class="alert alert-danger">Fehler: {html.escape(str(e)[:200])}</div>'
        )


@router.post("/api/system/packages/upgrade", response_class=HTMLResponse)
async def upgrade_packages(current_user: dict = Depends(require_perm("system", "control"))):
    """Fuehrt `apt upgrade -y` aus (ohne held-back Pakete)."""
    logger.info(f"Package-Upgrade gestartet von {current_user.get('username', 'Unbekannt')}")
    return await _run_apt_upgrade("/usr/local/sbin/dashboard-apt-upgrade", "System-Update")


@router.post("/api/system/packages/full-upgrade", response_class=HTMLResponse)
async def full_upgrade_packages(current_user: dict = Depends(require_perm("system", "control"))):
    """Fuehrt `apt full-upgrade -y` aus (inkl. held-back, kann Pakete entfernen)."""
    logger.info(f"Package-Full-Upgrade gestartet von {current_user.get('username', 'Unbekannt')}")
    return await _run_apt_upgrade("/usr/local/sbin/dashboard-apt-fullupgrade", "Full-Upgrade")


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
