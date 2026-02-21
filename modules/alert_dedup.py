"""
Alert-Deduplizierung

Verhindert doppelte Alerts innerhalb eines konfigurierbaren Zeitfensters.
Gleiche alert_type+target Kombinationen werden zusammengefasst und
erst nach Ablauf von min_interval_minutes erneut gesendet.
"""

from datetime import datetime, timedelta

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("alert_dedup")


async def should_send_alert(
    alert_type: str, target: str, min_interval_minutes: int = 30
) -> bool:
    """
    Prueft ob ein Alert gesendet werden soll oder unterdrueckt wird.

    - Kein unresolved Alert vorhanden: INSERT + return True
    - Unresolved Alert vorhanden, aber aelter als min_interval: UPDATE + return True
    - Unresolved Alert vorhanden und noch im Zeitfenster: return False (unterdruecken)
    - Bei DB-Fehler: return True (fail-open)

    Args:
        alert_type: Art des Alerts (z.B. "cpu_high", "disk_full")
        target: Betroffenes Ziel (z.B. Servername, IP)
        min_interval_minutes: Mindestabstand zwischen gleichen Alerts

    Returns:
        True wenn der Alert gesendet werden soll, False wenn unterdrueckt
    """
    try:
        db = await get_db()

        row = await db.execute_fetchall(
            "SELECT id, last_sent, send_count FROM alerts_sent "
            "WHERE alert_type = ? AND target = ? AND resolved = FALSE",
            (alert_type, target),
        )

        if not row:
            # Kein bestehender Alert — neuen Eintrag anlegen
            await db.execute(
                "INSERT INTO alerts_sent (alert_type, target) VALUES (?, ?)",
                (alert_type, target),
            )
            await db.commit()
            logger.debug(
                "Neuer Alert erstellt: type=%s target=%s", alert_type, target
            )
            return True

        entry = row[0]
        last_sent = datetime.fromisoformat(str(entry["last_sent"]))
        threshold = datetime.now() - timedelta(minutes=min_interval_minutes)

        if last_sent <= threshold:
            # Zeitfenster abgelaufen — erneut senden
            await db.execute(
                "UPDATE alerts_sent SET last_sent = CURRENT_TIMESTAMP, "
                "send_count = send_count + 1 "
                "WHERE id = ?",
                (entry["id"],),
            )
            await db.commit()
            logger.debug(
                "Alert erneut gesendet: type=%s target=%s count=%d",
                alert_type,
                target,
                entry["send_count"] + 1,
            )
            return True

        # Noch im Zeitfenster — unterdruecken
        logger.debug(
            "Alert unterdrueckt: type=%s target=%s (letzte Sendung: %s)",
            alert_type,
            target,
            last_sent,
        )
        return False

    except Exception:
        logger.exception("DB-Fehler bei should_send_alert — fail-open")
        return True


async def resolve_alert(alert_type: str, target: str) -> bool:
    """
    Markiert einen Alert als resolved.

    Args:
        alert_type: Art des Alerts
        target: Betroffenes Ziel

    Returns:
        True wenn ein Eintrag aktualisiert wurde
    """
    try:
        db = await get_db()
        cursor = await db.execute(
            "UPDATE alerts_sent SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP "
            "WHERE alert_type = ? AND target = ? AND resolved = FALSE",
            (alert_type, target),
        )
        await db.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(
                "Alert resolved: type=%s target=%s", alert_type, target
            )
        return updated
    except Exception:
        logger.exception("DB-Fehler bei resolve_alert")
        return False


async def get_active_alerts() -> list[dict]:
    """
    Gibt alle unresolved Alerts zurueck.

    Returns:
        Liste von Dicts mit alert_type, target, first_sent, last_sent, send_count
    """
    try:
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT alert_type, target, first_sent, last_sent, send_count "
            "FROM alerts_sent WHERE resolved = FALSE "
            "ORDER BY last_sent DESC"
        )
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("DB-Fehler bei get_active_alerts")
        return []


async def cleanup_old_alerts(days: int = 30) -> int:
    """
    Loescht resolved Alerts die aelter als X Tage sind.

    Args:
        days: Alter in Tagen ab dem geloescht wird

    Returns:
        Anzahl geloeschter Eintraege
    """
    try:
        db = await get_db()
        cursor = await db.execute(
            "DELETE FROM alerts_sent "
            "WHERE resolved = TRUE "
            "AND resolved_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("Alte Alerts bereinigt: %d Eintraege geloescht", deleted)
        return deleted
    except Exception:
        logger.exception("DB-Fehler bei cleanup_old_alerts")
        return 0
