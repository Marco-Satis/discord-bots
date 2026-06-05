"""
F44: IP-Security-Dashboard — Vereinheitlichte Sicherheitsuebersicht.

Zeigt den aktuellen Sicherheitsstatus: Fail2Ban-Jails und gebannte IPs,
SSL-Zertifikat-Status, Port-Erreichbarkeit sowie eine einheitliche
Uebersicht aller IP-Sperren (iptables, Fail2Ban, Blacklist, Bans aus SQLite).

Bietet Endpoints zum Entsperren von IPs und fuer Ban-Statistiken.
"""

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from modules.database.db_manager import get_db
from utils.config import DATA_DIR
from utils.logger import get_logger
from web.auth import require_auth, require_auth_api, require_perm

logger = get_logger("web.routes.security")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Security"])

MONITOR_DATA_DIR = DATA_DIR / "monitor"


def _read_json(filepath: Path) -> dict:
    """Liest eine JSON-Datei oder gibt leeres Dict zurueck."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"JSON lesen fehlgeschlagen: {filepath}: {e}")
    return {}


@router.get("/security", response_class=HTMLResponse)
async def security_page(request: Request, current_user: dict = Depends(require_auth)):
    """Sicherheits-Dashboard mit Fail2Ban, SSL und Port-Status."""
    # Fail2Ban-Status aus StatusWriter JSON lesen
    fail2ban_data = _read_json(MONITOR_DATA_DIR / "fail2ban_status.json")
    ssl_data = _read_json(MONITOR_DATA_DIR / "ssl_status.json")
    port_data = _read_json(MONITOR_DATA_DIR / "port_status.json")

    # F44: IP-Uebersicht fuer das Template sammeln
    ip_overview = await _collect_ip_overview(fail2ban_data)

    return templates.TemplateResponse("security.html", {
        "request": request,
        "user": current_user,
        "fail2ban": fail2ban_data,
        "ssl": ssl_data,
        "ports": port_data,
        "ip_overview": ip_overview,
    })


# ==============================================================
#  F44: Hilfsfunktionen — IP-Sperr-Quellen sammeln
# ==============================================================


async def _get_iptables_blocks() -> list[dict]:
    """Liest REJECT/DROP-Regeln aus iptables INPUT-Chain."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "iptables", "-L", "INPUT", "-n", "--line-numbers",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        output = stdout.decode("utf-8", errors="replace")
        blocks = []
        for line in output.strip().split("\n"):
            # Format: "num  REJECT  all  --  1.2.3.4  0.0.0.0/0  reject-with ..."
            if "REJECT" in line or "DROP" in line:
                parts = line.split()
                if len(parts) >= 5:
                    blocks.append({
                        "line_num": parts[0],
                        "action": parts[1],
                        "ip": parts[4],
                    })
        return blocks
    except Exception as exc:
        logger.debug(f"iptables auslesen fehlgeschlagen: {exc}")
        return []


def _get_fail2ban_bans(fail2ban_data: dict) -> list[dict]:
    """Extrahiert gebannte IPs aus den Fail2Ban-Statusdaten."""
    bans: list[dict] = []
    for entry in fail2ban_data.get("banned_ips", []):
        bans.append({
            "ip": entry.get("ip", ""),
            "jail": entry.get("jail", ""),
        })
    return bans


async def _get_blacklist_entries() -> list[dict]:
    """Liest alle Eintraege der Blacklist-Tabelle aus SQLite."""
    entries: list[dict] = []
    try:
        db = await get_db()
        cursor = await db.execute("SELECT * FROM blacklist ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        for row in rows:
            entries.append({
                "id": row["id"],
                "player_name": row["player_name"],
                "ip": row["ip_address"],
                "server_type": row["server_type"],
                "reason": row["reason"],
                "added_by": row["added_by"],
                "added_at": row["added_at"],
            })
    except Exception as exc:
        logger.debug(f"Blacklist lesen fehlgeschlagen: {exc}")
    return entries


async def _get_active_bans() -> list[dict]:
    """Liest alle aktiven Bans aus der SQLite bans-Tabelle."""
    bans: list[dict] = []
    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM bans WHERE active = 1 ORDER BY banned_at DESC"
        )
        rows = await cursor.fetchall()
        for row in rows:
            bans.append({
                "id": row["id"],
                "ip": row["ip_address"],
                "player_name": row["player_name"],
                "server_type": row["server_type"],
                "reason": row["reason"],
                "banned_by": row["banned_by"],
                "banned_at": row["banned_at"],
                "expires_at": row["expires_at"],
                "source": row["source"],
            })
    except Exception as exc:
        logger.debug(f"Bans lesen fehlgeschlagen: {exc}")
    return bans


async def _collect_ip_overview(fail2ban_data: dict | None = None) -> dict:
    """
    Sammelt alle IP-Sperren aus allen Quellen in einem Dict.
    Wird sowohl vom Template als auch vom API-Endpoint genutzt.
    """
    if fail2ban_data is None:
        fail2ban_data = _read_json(MONITOR_DATA_DIR / "fail2ban_status.json")

    iptables_blocks = await _get_iptables_blocks()
    fail2ban_bans = _get_fail2ban_bans(fail2ban_data)
    blacklist_entries = await _get_blacklist_entries()
    active_bans = await _get_active_bans()

    # Einheitliche Liste fuer die Tabelle aufbauen
    unified: list[dict] = []

    for entry in iptables_blocks:
        unified.append({
            "ip": entry["ip"],
            "source": "iptables",
            "detail": f"Regel #{entry['line_num']} — {entry['action']}",
            "timestamp": None,
            "extra": None,
        })

    for entry in fail2ban_bans:
        unified.append({
            "ip": entry["ip"],
            "source": "fail2ban",
            "detail": f"Jail: {entry['jail']}",
            "timestamp": None,
            "extra": entry.get("jail", ""),
        })

    for entry in blacklist_entries:
        unified.append({
            "ip": entry["ip"] or "—",
            "source": "blacklist",
            "detail": entry.get("reason") or f"Spieler: {entry.get('player_name', '?')}",
            "timestamp": entry.get("added_at"),
            "extra": entry.get("server_type"),
        })

    for entry in active_bans:
        unified.append({
            "ip": entry["ip"],
            "source": "ban",
            "detail": entry.get("reason") or f"Spieler: {entry.get('player_name', '?')}",
            "timestamp": entry.get("banned_at"),
            "extra": entry.get("server_type"),
        })

    return {
        "iptables": iptables_blocks,
        "fail2ban": fail2ban_bans,
        "blacklist": blacklist_entries,
        "bans": active_bans,
        "unified": unified,
        "total": len(unified),
    }


# ==============================================================
#  F44: API-Endpoint — IP-Uebersicht (JSON)
# ==============================================================


@router.get("/api/security/ip-overview")
async def api_ip_overview(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """Gibt alle IP-Sperren als JSON zurueck (iptables, Fail2Ban, Blacklist, Bans)."""
    try:
        overview = await _collect_ip_overview()
        return JSONResponse(content=overview)
    except Exception as exc:
        logger.error(f"IP-Uebersicht Fehler: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Fehler beim Sammeln der IP-Daten: {str(exc)[:200]}"},
        )


# ==============================================================
#  F44: API-Endpoint — IP entsperren
# ==============================================================

# Regex fuer IP-Validierung (IPv4)
_IP_PATTERN = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)

# Erlaubte Quellen fuer Unban
_VALID_SOURCES = {"iptables", "fail2ban", "blacklist", "ban"}


async def _audit_log_action(user: dict, action: str, target: str, details: str) -> None:
    """Schreibt eine Aktion ins Audit-Log (SQLite)."""
    try:
        db = await get_db()
        await db.execute(
            "INSERT INTO audit_log (user_id, user_name, action, target, details, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user.get("id", ""),
                user.get("username", "Unbekannt"),
                action,
                target,
                details,
                "dashboard",
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.error(f"Audit-Log schreiben fehlgeschlagen: {exc}")


@router.post("/api/security/unban")
async def api_unban_ip(request: Request, current_user: dict = Depends(require_perm("system", "control"))) -> HTMLResponse:
    """
    Entsperrt eine IP aus der angegebenen Quelle.
    Gibt HTML-Partial fuer HTMX zurueck.
    """
    user = current_user
    try:
        form = await request.form()
        ip = str(form.get("ip", "")).strip()
        source = str(form.get("source", "")).strip().lower()

        # Validierung
        if not ip:
            return HTMLResponse(
                '<div class="alert alert-danger">Keine IP-Adresse angegeben.</div>'
            )

        if source not in _VALID_SOURCES:
            return HTMLResponse(
                f'<div class="alert alert-danger">Ungueltige Quelle: {html.escape(source)}</div>'
            )

        # IP-Format pruefen (einfache Validierung, auch CIDR erlauben)
        ip_check = ip.split("/")[0]  # CIDR-Notation beruecksichtigen
        if not _IP_PATTERN.match(ip_check):
            return HTMLResponse(
                f'<div class="alert alert-danger">Ungueltige IP-Adresse: {html.escape(ip)}</div>'
            )

        username = user.get("username", "Unbekannt")

        # --- iptables ---
        if source == "iptables":
            proc = await asyncio.create_subprocess_exec(
                "sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "REJECT",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            if proc.returncode != 0:
                # Evtl. DROP statt REJECT
                proc2 = await asyncio.create_subprocess_exec(
                    "sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "DROP",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=10.0)
                if proc2.returncode != 0:
                    error_msg = stderr.decode("utf-8", errors="replace").strip()
                    return HTMLResponse(
                        f'<div class="alert alert-danger">iptables Fehler: {html.escape(error_msg[:200])}</div>'
                    )

            logger.info(f"iptables Unban: {ip} von {username}")
            await _audit_log_action(user, "ip_unban_iptables", ip, f"iptables Regel entfernt von {username}")

        # --- fail2ban ---
        elif source == "fail2ban":
            jail = str(form.get("jail", "")).strip()
            if not jail:
                # Versuche default Jail
                jail = "sshd"

            # Defense-in-Depth: jail muss alphanumerisch + dash/underscore sein
            # (fail2ban-jails haben Konvention "sshd", "minecraft-bmc", "nginx-auth").
            # Verhindert command-injection ueber form-data trotz subprocess_exec.
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$", jail):
                return HTMLResponse(
                    f'<div class="alert alert-danger">Ungueltiger Jail-Name: {html.escape(jail[:50])}</div>'
                )

            proc = await asyncio.create_subprocess_exec(
                "sudo", "fail2ban-client", "set", jail, "unbanip", ip,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                return HTMLResponse(
                    f'<div class="alert alert-danger">Fail2Ban Fehler: {html.escape(error_msg[:200])}</div>'
                )

            logger.info(f"Fail2Ban Unban: {ip} (Jail: {jail}) von {username}")
            await _audit_log_action(user, "ip_unban_fail2ban", ip, f"Fail2Ban Unban Jail={jail} von {username}")

        # --- blacklist ---
        elif source == "blacklist":
            db = await get_db()
            cursor = await db.execute(
                "DELETE FROM blacklist WHERE ip_address = ?", (ip,)
            )
            await db.commit()
            deleted = cursor.rowcount

            if deleted == 0:
                return HTMLResponse(
                    f'<div class="alert alert-danger">IP {html.escape(ip)} nicht in Blacklist gefunden.</div>'
                )

            logger.info(f"Blacklist Unban: {ip} ({deleted} Eintraege) von {username}")
            await _audit_log_action(user, "ip_unban_blacklist", ip, f"{deleted} Blacklist-Eintraege entfernt von {username}")

        # --- ban ---
        elif source == "ban":
            db = await get_db()
            cursor = await db.execute(
                "UPDATE bans SET active = 0 WHERE ip_address = ? AND active = 1", (ip,)
            )
            await db.commit()
            updated = cursor.rowcount

            if updated == 0:
                return HTMLResponse(
                    f'<div class="alert alert-danger">Kein aktiver Ban fuer {html.escape(ip)} gefunden.</div>'
                )

            logger.info(f"Ban deaktiviert: {ip} ({updated} Eintraege) von {username}")
            await _audit_log_action(user, "ip_unban_ban", ip, f"{updated} Bans deaktiviert von {username}")

        return HTMLResponse(
            f'<div class="alert alert-success">{html.escape(ip)} erfolgreich entsperrt ({html.escape(source)}).</div>'
        )

    except asyncio.TimeoutError:
        return HTMLResponse(
            '<div class="alert alert-danger">Timeout — Befehl reagiert nicht.</div>'
        )
    except Exception as exc:
        logger.error(f"Unban Fehler: {exc}")
        return HTMLResponse(
            f'<div class="alert alert-danger">Fehler: {html.escape(str(exc)[:200])}</div>'
        )


# ==============================================================
#  F44: API-Endpoint — Ban-Statistiken
# ==============================================================


@router.get("/api/security/ban-stats")
async def api_ban_stats(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """Gibt Ban-Statistiken zurueck: Gesamt, letzte 7 Tage, haeufigste IPs."""
    try:
        db = await get_db()

        # Gesamtzahl aktiver Bans
        cursor = await db.execute("SELECT COUNT(*) FROM bans WHERE active = 1")
        row = await cursor.fetchone()
        total_active = row[0] if row else 0

        # Bans in den letzten 7 Tagen
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM bans WHERE banned_at >= ?",
            (seven_days_ago,),
        )
        row = await cursor.fetchone()
        last_7_days = row[0] if row else 0

        # Haeufigste gebannte IPs (Top 10)
        cursor = await db.execute(
            "SELECT ip_address, COUNT(*) as ban_count "
            "FROM bans "
            "GROUP BY ip_address "
            "ORDER BY ban_count DESC "
            "LIMIT 10"
        )
        rows = await cursor.fetchall()
        top_ips = [
            {"ip": r["ip_address"], "count": r["ban_count"]}
            for r in rows
        ]

        # Blacklist-Eintraege zaehlen
        cursor = await db.execute("SELECT COUNT(*) FROM blacklist")
        row = await cursor.fetchone()
        blacklist_count = row[0] if row else 0

        return JSONResponse(content={
            "total_active_bans": total_active,
            "bans_last_7_days": last_7_days,
            "blacklist_count": blacklist_count,
            "top_banned_ips": top_ips,
        })

    except Exception as exc:
        logger.error(f"Ban-Statistiken Fehler: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Fehler: {str(exc)[:200]}"},
        )
