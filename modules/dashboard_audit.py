"""
Dashboard-Audit-Log (B4) — wer hat was im Web-Dashboard geaendert.

Schreibt in die Tabelle `dashboard_audit` (Migration v9). Jede `edit`/`control`-
Aktion ueber das Dashboard soll hier einen Eintrag erzeugen (Config-Save, Restart,
Rollen-Mapping-Aenderung, kuenftig Ban). Best-Effort: ein Fehler beim Loggen darf
NIE die eigentliche Aktion scheitern lassen.

PII-Hygiene: `discord_id` ist als DSGVO-Audit-Trail ok; in `detail` duerfen
KEINE Secrets stehen (Tokens, Passwoerter, Webhook-URLs). Caller ist dafuer
verantwortlich, nur unkritische Aenderungs-Infos zu uebergeben.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from modules.database.db_manager import get_db, get_read_db
from utils.logger import get_logger

logger = get_logger("dashboard_audit")

# Felder die NIE in detail landen duerfen (Defense-in-Depth gegen Secret-Leak)
_SECRET_KEYS = (
    "password", "passwort", "pass_hash", "token", "secret", "webhook",
    "api_key", "apikey", "client_secret", "rcon", "private",
)


def _sanitize_detail(detail: Optional[dict]) -> Optional[str]:
    """
    Wandelt `detail` in JSON-Text um und redigiert verdaechtige Secret-Felder.
    Gibt None zurueck wenn detail leer ist. Nie fatal.
    """
    if not detail:
        return None
    try:
        safe: dict[str, Any] = {}
        for key, value in detail.items():
            lk = str(key).lower()
            if any(s in lk for s in _SECRET_KEYS):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = value
        return json.dumps(safe, ensure_ascii=False, default=str)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Audit-detail nicht serialisierbar: {e}")
        return None


async def log_action(
    discord_id: Optional[str],
    username: Optional[str],
    resource: str,
    action: str,
    detail: Optional[dict] = None,
    ip: Optional[str] = None,
) -> None:
    """
    Schreibt einen Audit-Eintrag. Best-Effort — schluckt alle Fehler.

    Args:
        discord_id: Discord-User-ID des Akteurs (oder local:<user> beim Fallback).
        username:   Anzeigename des Akteurs.
        resource:   Bereich (siehe modules.rbac.RESOURCES).
        action:     view | edit | control.
        detail:     dict mit Aenderungs-Infos (KEINE Secrets) — wird JSON-encoded.
        ip:         Client-IP.
    """
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO dashboard_audit "
            "(discord_id, username, resource, action, detail, ip) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(discord_id) if discord_id is not None else None,
                str(username) if username is not None else None,
                str(resource),
                str(action),
                _sanitize_detail(detail),
                str(ip) if ip is not None else None,
            ),
        )
        await conn.commit()
    except Exception as e:  # noqa: BLE001 — Audit darf nie die Aktion kippen
        logger.error(f"Audit-Write fehlgeschlagen ({resource}/{action}): {e}")


async def log_from_user(
    user: Optional[dict],
    resource: str,
    action: str,
    detail: Optional[dict] = None,
    ip: Optional[str] = None,
) -> None:
    """Convenience: Audit-Eintrag direkt aus einem current_user-Dict."""
    user = user or {}
    await log_action(
        discord_id=user.get("sub"),
        username=user.get("username"),
        resource=resource,
        action=action,
        detail=detail,
        ip=ip,
    )


async def fetch_audit(
    user_q: str = "",
    resource_q: str = "",
    since: str = "",
    until: str = "",
    limit: int = 200,
) -> list[dict]:
    """
    Liest Audit-Eintraege mit optionalen Filtern (fuer die /audit-Seite).

    Alle Filter parametrisiert (CWE-89 safe). `user_q` matcht discord_id ODER
    username (Teilstring). `resource_q` exakt. `since`/`until` als ISO-Zeit-Praefix
    (Vergleich auf der ts-Spalte). `limit` 1..1000.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if user_q:
        clauses.append("(discord_id LIKE ? OR username LIKE ?)")
        like = f"%{user_q}%"
        params.extend([like, like])
    if resource_q:
        clauses.append("resource = ?")
        params.append(resource_q)
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if until:
        clauses.append("ts <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        lim = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        lim = 200

    sql = f"SELECT id, ts, discord_id, username, resource, action, detail, ip FROM dashboard_audit{where} ORDER BY ts DESC, id DESC LIMIT ?"  # nosec B608 - where nur aus festen Klausel-Strings, alle Werte parametrisiert
    params.append(lim)

    rows_out: list[dict] = []
    try:
        conn = await get_read_db()
        cursor = await conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        for r in rows:
            rows_out.append({
                "id": r[0],
                "ts": r[1],
                "discord_id": r[2],
                "username": r[3],
                "resource": r[4],
                "action": r[5],
                "detail": r[6],
                "ip": r[7],
            })
    except Exception as e:  # noqa: BLE001
        logger.error(f"fetch_audit fehlgeschlagen: {e}")
    return rows_out
