"""
F28: JSON → SQLite Importer

Importiert bestehende JSON-Daten in die SQLite-Datenbank.
WICHTIG: JSON-Dateien werden NICHT geloescht oder umbenannt!
Der Import ist idempotent — bei erneutem Aufruf werden nur
fehlende Daten nachimportiert.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from utils.config import DATA_DIR, MONITOR_DATA_DIR, ADMIN_DATA_DIR
from utils.logger import get_logger

logger = get_logger("database.importer")


def _safe_json_load(path: Path) -> Optional[Any]:
    """Laedt eine JSON-Datei sicher. Gibt None bei Fehler zurueck."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"Konnte {path} nicht laden: {e}")
    return None


def _parse_iso(val: Any) -> Optional[str]:
    """Normalisiert einen ISO-Timestamp-String."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return s


async def import_all(db: aiosqlite.Connection) -> dict:
    """
    Importiert alle bekannten JSON-Dateien in die SQLite-Datenbank.
    JSON-Dateien bleiben intakt (werden NICHT umbenannt oder geloescht).

    Returns:
        Dict mit Import-Statistiken pro Tabelle
    """
    stats = {}
    logger.info("=== JSON → SQLite Import gestartet ===")

    # Je Tabelle einzeln kapseln und committen. Vorher lief alles in einer
    # Kette mit einem Commit am Ende: ein Fehler in der Mitte riss die
    # nachfolgenden Importe mit, und das bereits Importierte war ebenfalls weg.
    schritte = [
        ("players", _import_player_stats),
        ("player_ips", _import_player_ips),
        ("stats_history", _import_stats_history),
        ("events", _import_events),
        ("warns", _import_warns),
        ("tickets", _import_tickets),
        ("leveling", _import_leveling),
        ("giveaways", _import_giveaways),
        ("whitelist", _import_whitelist),
        ("blacklist", _import_blacklist),
        ("mc_blacklist", _import_mc_blacklist),
        ("backup_history", _import_backup_history),
        ("reaction_roles", _import_reaction_roles),
    ]
    fehlgeschlagen = []
    for name, funktion in schritte:
        try:
            stats[name] = await funktion(db)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            stats[name] = 0
            fehlgeschlagen.append(name)
            logger.error(f"Import '{name}' fehlgeschlagen: {e}", exc_info=True)

    if fehlgeschlagen:
        logger.error(
            f"Import unvollstaendig — diese Tabellen fehlen: "
            f"{', '.join(fehlgeschlagen)}"
        )
        stats["_fehlgeschlagen"] = fehlgeschlagen

    total = sum(v for v in stats.values() if isinstance(v, int))
    if fehlgeschlagen:
        logger.warning(
            f"=== Import mit Luecken beendet: {total} Eintraege, "
            f"{len(fehlgeschlagen)} Tabelle(n) fehlgeschlagen ==="
        )
    else:
        logger.info(f"=== Import abgeschlossen: {total} Eintraege gesamt ===")
    for table, count in stats.items():
        if isinstance(count, int) and count > 0:
            logger.info(f"  {table}: {count} Eintraege")

    return stats


async def _import_player_stats(db: aiosqlite.Connection) -> int:
    """Importiert player_stats.json → players + player_sessions Tabellen."""
    # Satisfactory Player Stats
    count = 0
    for data_file in [
        DATA_DIR / "player_stats.json",
        DATA_DIR / "gameserver" / "player_stats.json",
        DATA_DIR / "monitor" / "player_stats.json",
    ]:
        data = _safe_json_load(data_file)
        if not data or not isinstance(data, dict):
            continue

        server_type = "satisfactory"  # Default
        if "mc_" in str(data_file):
            server_type = "minecraft"

        for name, info in data.items():
            if not isinstance(info, dict):
                continue

            # Spieler in players-Tabelle einfuegen (falls nicht vorhanden)
            first_seen = _parse_iso(info.get("first_seen"))
            last_seen = _parse_iso(info.get("last_seen"))
            total_minutes = info.get("total_playtime_minutes", 0)

            if not first_seen:
                first_seen = datetime.now().isoformat()
            if not last_seen:
                last_seen = first_seen

            try:
                await db.execute(
                    "INSERT OR IGNORE INTO players "
                    "(name, first_seen, last_seen, total_playtime_seconds, server_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, first_seen, last_seen, total_minutes * 60, server_type)
                )

                # Player-ID holen
                cursor = await db.execute(
                    "SELECT id FROM players WHERE name = ?", (name,)
                )
                row = await cursor.fetchone()
                if not row:
                    continue
                player_id = row[0]

                # Update falls Spieler schon existierte
                await db.execute(
                    "UPDATE players SET last_seen = MAX(last_seen, ?), "
                    "total_playtime_seconds = MAX(total_playtime_seconds, ?) "
                    "WHERE id = ?",
                    (last_seen, total_minutes * 60, player_id)
                )

                # Sessions importieren
                sessions = info.get("sessions", [])
                for sess in sessions:
                    join_time = _parse_iso(sess.get("join"))
                    leave_time = _parse_iso(sess.get("leave"))
                    duration_min = sess.get("duration_minutes", 0)

                    if not join_time:
                        continue

                    # Duplikat-Check via join_time
                    existing = await db.execute(
                        "SELECT id FROM player_sessions "
                        "WHERE player_id = ? AND join_time = ?",
                        (player_id, join_time)
                    )
                    if await existing.fetchone():
                        continue

                    await db.execute(
                        "INSERT INTO player_sessions "
                        "(player_id, server_type, join_time, leave_time, duration_seconds) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (player_id, server_type, join_time, leave_time,
                         duration_min * 60 if duration_min else None)
                    )
                    count += 1

                count += 1  # Spieler selbst
            except Exception as e:
                logger.warning(f"Fehler beim Import von Spieler {name}: {e}")

        logger.info(f"Player-Import aus {data_file.name}: abgeschlossen")
        break  # Erste gefundene Datei reicht

    return count


async def _import_player_ips(db: aiosqlite.Connection) -> int:
    """Importiert player_ip_tracker_*.json → player_ips Tabelle."""
    count = 0

    # Alle IP-Tracker-Dateien suchen
    ip_files = list(DATA_DIR.glob("player_ip_tracker*.json"))
    ip_files.extend(DATA_DIR.glob("*/player_ip_tracker*.json"))

    for data_file in ip_files:
        data = _safe_json_load(data_file)
        if not data or not isinstance(data, dict):
            continue

        ip_map = data.get("ip_map", {})
        if not ip_map:
            continue

        # Server-Typ aus Dateiname ableiten
        server_type = "satisfactory"
        fname = data_file.stem.lower()
        if "mc" in fname or "minecraft" in fname:
            server_type = "minecraft"

        for player_name, info in ip_map.items():
            if not isinstance(info, dict):
                continue

            ip = info.get("ip")
            if not ip:
                continue

            # Player-ID finden oder erstellen
            cursor = await db.execute(
                "SELECT id FROM players WHERE name = ?", (player_name,)
            )
            row = await cursor.fetchone()
            if not row:
                # Spieler erstellen
                now = datetime.now().isoformat()
                await db.execute(
                    "INSERT OR IGNORE INTO players "
                    "(name, first_seen, last_seen, server_type) "
                    "VALUES (?, ?, ?, ?)",
                    (player_name, now, now, server_type)
                )
                cursor = await db.execute(
                    "SELECT id FROM players WHERE name = ?", (player_name,)
                )
                row = await cursor.fetchone()
                if not row:
                    continue

            player_id = row[0]
            first_seen = _parse_iso(info.get("first_seen"))
            last_seen = _parse_iso(info.get("last_seen"))

            if not first_seen:
                first_seen = datetime.now().isoformat()
            if not last_seen:
                last_seen = first_seen

            # Duplikat-Check
            existing = await db.execute(
                "SELECT id FROM player_ips "
                "WHERE player_id = ? AND ip_address = ? AND server_type = ?",
                (player_id, ip, server_type)
            )
            if await existing.fetchone():
                # Update last_seen
                await db.execute(
                    "UPDATE player_ips SET last_seen = MAX(last_seen, ?) "
                    "WHERE player_id = ? AND ip_address = ? AND server_type = ?",
                    (last_seen, player_id, ip, server_type)
                )
                continue

            await db.execute(
                "INSERT INTO player_ips "
                "(player_id, ip_address, first_seen, last_seen, server_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (player_id, ip, first_seen, last_seen, server_type)
            )
            count += 1

        # Bans importieren
        bans = data.get("bans", {})
        for player_name, ban_info in bans.items():
            if not isinstance(ban_info, dict):
                continue
            ip = ban_info.get("ip")
            if not ip:
                continue

            # Duplikat-Check
            existing = await db.execute(
                "SELECT id FROM bans WHERE ip_address = ? AND player_name = ? AND active = TRUE",
                (ip, player_name)
            )
            if await existing.fetchone():
                continue

            await db.execute(
                "INSERT INTO bans "
                "(ip_address, player_name, server_type, reason, banned_by, banned_at, active, source) "
                "VALUES (?, ?, ?, ?, ?, ?, TRUE, ?)",
                (ip, player_name, server_type,
                 ban_info.get("reason", "Imported from JSON"),
                 ban_info.get("banned_by", "system"),
                 _parse_iso(ban_info.get("banned_at")),
                 "json_import")
            )
            count += 1

    return count


async def _import_stats_history(db: aiosqlite.Connection) -> int:
    """Importiert stats_history.json → stats_history Tabelle."""
    count = 0

    data_file = MONITOR_DATA_DIR / "stats_history.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    entries = data.get("entries", [])
    if not entries:
        return 0

    # Batch-Import fuer Performance
    batch = []
    for entry in entries:
        timestamp = _parse_iso(entry.get("timestamp"))
        if not timestamp:
            continue

        system = entry.get("system", {})
        servers = entry.get("servers", [])

        batch.append((
            timestamp,
            system.get("cpu_percent"),
            system.get("ram_percent"),
            system.get("ram_used_gb"),
            system.get("disk_percent"),
            system.get("disk_used_gb"),
            json.dumps(servers) if servers else None,
        ))

    if batch:
        # Pruefen ob bereits Daten vorhanden
        cursor = await db.execute("SELECT COUNT(*) FROM stats_history")
        row = await cursor.fetchone()
        existing_count = row[0] if row else 0

        if existing_count == 0:
            await db.executemany(
                "INSERT INTO stats_history "
                "(timestamp, cpu_percent, ram_percent, ram_used_gb, "
                "disk_percent, disk_used_gb, server_data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch
            )
            count = len(batch)
            logger.info(f"Stats-History: {count} Eintraege importiert")
        else:
            logger.info(
                f"Stats-History: Uebersprungen ({existing_count} Eintraege bereits vorhanden)"
            )

    return count


async def _import_events(db: aiosqlite.Connection) -> int:
    """Importiert events.json → events Tabelle."""
    count = 0

    data_file = MONITOR_DATA_DIR / "events.json"
    data = _safe_json_load(data_file)
    if not data:
        return 0

    events = []
    if isinstance(data, dict):
        events = data.get("events", [])
    elif isinstance(data, list):
        events = data

    # Pruefen ob bereits Events vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM events")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Events: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    for evt in events:
        timestamp = _parse_iso(evt.get("timestamp"))
        if not timestamp:
            continue

        await db.execute(
            "INSERT INTO events (timestamp, event_type, category, message) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, evt.get("type", "info"), "system", evt.get("message", ""))
        )
        count += 1

    return count


async def _import_warns(db: aiosqlite.Connection) -> int:
    """Importiert warns.json → warns Tabelle."""
    count = 0

    data_file = ADMIN_DATA_DIR / "warns.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Warns vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM warns")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Warns: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    for user_id, user_data in data.items():
        if not isinstance(user_data, dict):
            continue

        warns_list = user_data.get("warns", [])
        for warn in warns_list:
            if not isinstance(warn, dict):
                continue

            await db.execute(
                "INSERT INTO warns "
                "(user_id, moderator_id, reason, points, created_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id,
                 warn.get("warned_by", "unknown"),
                 warn.get("reason", "Kein Grund"),
                 warn.get("points", 1),
                 _parse_iso(warn.get("warned_at")),
                 not warn.get("expired", False))
            )
            count += 1

    return count


async def _import_tickets(db: aiosqlite.Connection) -> int:
    """Importiert tickets.json → tickets + ticket_messages Tabellen."""
    count = 0

    data_file = ADMIN_DATA_DIR / "tickets.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Tickets vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM tickets")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Tickets: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    tickets = data.get("tickets", {})
    for ticket_id_str, ticket in tickets.items():
        if not isinstance(ticket, dict):
            continue

        await db.execute(
            "INSERT INTO tickets "
            "(channel_id, user_id, subject, status, created_at, closed_at, closed_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(ticket.get("channel_id", "")),
             str(ticket.get("user_id", "")),
             ticket.get("subject"),
             ticket.get("status", "open"),
             _parse_iso(ticket.get("created_at")),
             _parse_iso(ticket.get("closed_at")),
             str(ticket.get("closed_by", "")) if ticket.get("closed_by") else None)
        )
        count += 1

        # Ticket-ID holen
        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        db_ticket_id = row[0] if row else 0

        # Transcript importieren
        transcript = ticket.get("transcript", [])
        for msg in transcript:
            if not isinstance(msg, dict):
                continue
            await db.execute(
                "INSERT INTO ticket_messages "
                "(ticket_id, user_id, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (db_ticket_id,
                 str(msg.get("author_id", "")),
                 msg.get("content", ""),
                 _parse_iso(msg.get("timestamp")))
            )
            count += 1

    return count


async def _import_leveling(db: aiosqlite.Connection) -> int:
    """Importiert leveling.json → leveling Tabelle."""
    count = 0

    data_file = ADMIN_DATA_DIR / "leveling.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Leveling-Daten vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM leveling")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Leveling: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    # Dieselbe Quelle, die auch die Schema-Migration zum Backfill benutzt.
    from utils.config import get_env
    guild_id = str(get_env("GUILD_ID", "0") or "0")
    if guild_id == "0":
        logger.warning(
            "Leveling-Import: GUILD_ID fehlt — die Daten landen in der "
            "Default-Guild '0' und tauchen im Leaderboard der echten Guild "
            "nicht auf. GUILD_ID setzen und erneut importieren."
        )
    uebersprungen = 0

    for user_id, info in data.items():
        if not isinstance(info, dict):
            continue

        # guild_id ist seit Schema v6 NOT NULL. Ohne die Spalte scheiterte jedes
        # INSERT an der Constraint — und weil INSERT OR IGNORE das verschluckt
        # und der Zaehler trotzdem hochlief, meldete der Import Erfolg, obwohl
        # null Zeilen geschrieben wurden. Im Wiederherstellungsfall waere damit
        # die gesamte XP-Historie still verlorengegangen.
        cursor = await db.execute(
            "INSERT OR IGNORE INTO leveling "
            "(guild_id, user_id, xp, level, messages, voice_minutes, last_xp_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id,
             user_id,
             info.get("xp", 0),
             info.get("level", 0),
             info.get("total_messages", 0),
             info.get("voice_minutes", 0),
             _parse_iso(info.get("last_xp_at")))
        )
        # Nur zaehlen, was wirklich geschrieben wurde.
        if cursor.rowcount:
            count += cursor.rowcount
        else:
            uebersprungen += 1

    if uebersprungen:
        logger.warning(
            f"Leveling: {uebersprungen} Eintraege nicht geschrieben "
            f"(bereits vorhanden oder Constraint) — {count} importiert"
        )
    return count


async def _import_giveaways(db: aiosqlite.Connection) -> int:
    """Importiert giveaways.json → giveaways + giveaway_participants Tabellen."""
    count = 0

    data_file = ADMIN_DATA_DIR / "giveaways.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Giveaways vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM giveaways")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Giveaways: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    for msg_id, info in data.items():
        if not isinstance(info, dict):
            continue

        await db.execute(
            "INSERT OR IGNORE INTO giveaways "
            "(message_id, channel_id, guild_id, prize, winner_count, "
            "ends_at, ended, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(msg_id),
             str(info.get("channel_id", "")),
             str(info.get("guild_id", "")),
             info.get("prize", ""),
             info.get("winners_count", 1),
             _parse_iso(info.get("ends_at")),
             info.get("ended", False),
             _parse_iso(info.get("created_at")))
        )
        count += 1

        # Giveaway-ID holen
        cursor = await db.execute(
            "SELECT id FROM giveaways WHERE message_id = ?", (str(msg_id),)
        )
        row = await cursor.fetchone()
        if not row:
            continue
        giveaway_id = row[0]

        # Teilnehmer importieren
        participants = info.get("participants", [])
        for uid in participants:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO giveaway_participants "
                    "(giveaway_id, user_id) VALUES (?, ?)",
                    (giveaway_id, str(uid))
                )
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

    return count


async def _import_whitelist(db: aiosqlite.Connection) -> int:
    """Importiert whitelist.json → whitelist Tabelle."""
    count = 0

    data_file = DATA_DIR / "whitelist.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Eintraege vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM whitelist")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Whitelist: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    players = data.get("players", [])
    for entry in players:
        if not isinstance(entry, dict):
            continue

        await db.execute(
            "INSERT OR IGNORE INTO whitelist "
            "(player_name, server_type, added_by, added_at) "
            "VALUES (?, ?, ?, ?)",
            (entry.get("name", ""),
             "satisfactory",
             entry.get("added_by"),
             _parse_iso(entry.get("added_at")))
        )
        count += 1

    return count


async def _import_blacklist(db: aiosqlite.Connection) -> int:
    """Importiert blacklist.json → blacklist Tabelle."""
    count = 0

    data_file = DATA_DIR / "blacklist.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Eintraege vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM blacklist")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(f"Blacklist: Uebersprungen ({row[0]} Eintraege bereits vorhanden)")
        return 0

    players = data.get("players", [])
    for entry in players:
        if not isinstance(entry, dict):
            continue

        await db.execute(
            "INSERT OR IGNORE INTO blacklist "
            "(player_name, server_type, reason, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (entry.get("name", ""),
             "satisfactory",
             entry.get("reason"),
             entry.get("banned_by"),
             _parse_iso(entry.get("banned_at")))
        )
        count += 1

    return count


async def _import_mc_blacklist(db: aiosqlite.Connection) -> int:
    """Importiert mc_blacklist.json → blacklist + bans Tabellen."""
    count = 0

    data_file = DATA_DIR / "mc_blacklist.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    bans = data.get("bans", [])
    for ban in bans:
        if not isinstance(ban, dict):
            continue

        player_name = ban.get("player_name", "")
        servers = ban.get("servers", ["ALL"])
        active = ban.get("active", True)

        # Server-Typ bestimmen
        for srv in servers:
            srv_lower = str(srv).lower()
            if srv_lower == "all":
                server_type = "minecraft"
            elif "bmc" in srv_lower:
                server_type = "mc_bmc"
            elif "vanilla" in srv_lower:
                server_type = "mc_vanilla"
            else:
                server_type = "minecraft"

            # In blacklist Tabelle
            await db.execute(
                "INSERT OR IGNORE INTO blacklist "
                "(player_name, ip_address, server_type, reason, added_by, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (player_name,
                 ban.get("ip"),
                 server_type,
                 ban.get("reason"),
                 ban.get("banned_by"),
                 _parse_iso(ban.get("timestamp")))
            )
            count += 1

            # Wenn aktiv und IP vorhanden, auch in bans Tabelle
            if active and ban.get("ip"):
                existing = await db.execute(
                    "SELECT id FROM bans "
                    "WHERE ip_address = ? AND player_name = ? AND active = TRUE",
                    (ban["ip"], player_name)
                )
                if not await existing.fetchone():
                    await db.execute(
                        "INSERT INTO bans "
                        "(ip_address, player_name, server_type, reason, "
                        "banned_by, banned_at, active, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, TRUE, ?)",
                        (ban["ip"], player_name, server_type,
                         ban.get("reason"),
                         ban.get("banned_by"),
                         _parse_iso(ban.get("timestamp")),
                         "json_import")
                    )

    return count


async def _import_backup_history(db: aiosqlite.Connection) -> int:
    """Importiert backup_history.json → backup_history Tabelle."""
    count = 0

    data_file = DATA_DIR / "backup_history.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    # Pruefen ob bereits Eintraege vorhanden
    cursor = await db.execute("SELECT COUNT(*) FROM backup_history")
    row = await cursor.fetchone()
    if row and row[0] > 0:
        logger.info(
            f"Backup-History: Uebersprungen ({row[0]} Eintraege bereits vorhanden)"
        )
        return 0

    backups = data.get("backups", [])
    for entry in backups:
        if not isinstance(entry, dict):
            continue

        await db.execute(
            "INSERT INTO backup_history "
            "(server_type, backup_type, filename, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("satisfactory",
             entry.get("type", "unknown"),
             entry.get("filename", ""),
             entry.get("size_bytes", 0),
             _parse_iso(entry.get("created_at")))
        )
        count += 1

    return count


async def _import_reaction_roles(db: aiosqlite.Connection) -> int:
    """Importiert reaction_roles.json → reaction_roles Tabelle."""
    data_file = ADMIN_DATA_DIR / "reaction_roles.json"
    data = _safe_json_load(data_file)
    if not data or not isinstance(data, dict):
        return 0

    count = 0
    for msg_id_str, entry in data.items():
        channel_id = entry.get("channel_id")
        guild_id = entry.get("guild_id")
        roles_map = entry.get("roles", {})

        if not channel_id or not guild_id:
            continue

        for emoji_str, role_id in roles_map.items():
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO reaction_roles "
                    "(message_id, channel_id, guild_id, emoji, role_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (msg_id_str, str(channel_id), str(guild_id),
                     emoji_str, str(role_id))
                )
                count += 1
            except Exception as e:
                logger.warning(
                    f"Reaction-Role Import fehlgeschlagen "
                    f"(msg={msg_id_str}, emoji={emoji_str}): {e}"
                )

    if count > 0:
        logger.info(f"reaction_roles: {count} Eintraege importiert")
    return count


async def check_import_needed(db: aiosqlite.Connection) -> bool:
    """
    Prueft ob ein JSON-Import noetig ist.
    Gibt True zurueck wenn die DB leer ist aber JSON-Dateien existieren.
    """
    # Pruefen ob Haupt-Tabellen leer sind (Whitelist hardcoded oben — sicher)
    for table in ["players", "stats_history", "events", "warns", "leveling"]:
        try:
            cursor = await db.execute(f"SELECT COUNT(*) FROM [{table}]")  # nosec B608 - table aus hardcoded Whitelist
            row = await cursor.fetchone()
            if row and row[0] > 0:
                return False  # Bereits Daten vorhanden
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

    # Pruefen ob JSON-Dateien existieren
    json_files = [
        DATA_DIR / "player_stats.json",
        MONITOR_DATA_DIR / "stats_history.json",
        MONITOR_DATA_DIR / "events.json",
        ADMIN_DATA_DIR / "warns.json",
        ADMIN_DATA_DIR / "leveling.json",
    ]
    return any(f.exists() for f in json_files)
