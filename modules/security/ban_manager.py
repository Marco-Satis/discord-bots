"""
Ban Manager — Ban-Expiry and Boot-Restore for iptables-based IP bans.

Manages persistent bans stored in SQLite with automatic expiry
and restoration of iptables rules on bot startup.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from modules.database.db_manager import get_db
from utils.logger import get_logger

logger = get_logger("security.ban_manager")


class BanManager:
    """Manages IP bans with SQLite persistence and iptables enforcement."""

    def __init__(self) -> None:
        """Minimal setup — database and iptables are handled lazily."""
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_cmd(*args: str) -> tuple[int, str]:
        """Run a shell command asynchronously.

        Args:
            *args: Command and its arguments.

        Returns:
            Tuple of (returncode, stderr output).
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return proc.returncode, stderr.decode().strip()

    async def _iptables_block(self, ip: str) -> None:
        """Add INPUT + OUTPUT REJECT rules for the given IP.

        Args:
            ip: The IP address to block.
        """
        rc_in, err_in = await self._run_cmd(
            "sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "REJECT"
        )
        if rc_in != 0:
            logger.error("iptables INPUT block failed for %s: %s", ip, err_in)

        rc_out, err_out = await self._run_cmd(
            "sudo", "iptables", "-I", "OUTPUT", "-d", ip, "-j", "REJECT"
        )
        if rc_out != 0:
            logger.error("iptables OUTPUT block failed for %s: %s", ip, err_out)

        if rc_in == 0 and rc_out == 0:
            logger.info("iptables block applied for %s", ip)

    async def _iptables_unblock(self, ip: str) -> None:
        """Remove INPUT + OUTPUT REJECT rules for the given IP.

        Args:
            ip: The IP address to unblock.
        """
        rc_in, err_in = await self._run_cmd(
            "sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "REJECT"
        )
        if rc_in != 0:
            logger.warning("iptables INPUT unblock failed for %s: %s", ip, err_in)

        rc_out, err_out = await self._run_cmd(
            "sudo", "iptables", "-D", "OUTPUT", "-d", ip, "-j", "REJECT"
        )
        if rc_out != 0:
            logger.warning("iptables OUTPUT unblock failed for %s: %s", ip, err_out)

        if rc_in == 0 and rc_out == 0:
            logger.info("iptables unblock applied for %s", ip)

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    async def restore_bans(self) -> int:
        """Boot-Restore: re-apply iptables rules for all active bans.

        Bans whose ``expires_at`` is in the past are deactivated
        instead of being restored.

        Returns:
            Number of bans that were actually restored (iptables rules applied).
        """
        db = await get_db()
        now = datetime.now(timezone.utc)
        restored = 0

        try:
            cursor = await db.execute("SELECT * FROM bans WHERE active = TRUE")
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.error("Failed to query active bans for restore: %s", exc)
            return 0

        logger.info("Boot-Restore: found %d active ban(s) in database", len(rows))

        for row in rows:
            ip = row["ip_address"]
            expires_at_raw = row["expires_at"]

            # Check if the ban has already expired
            if expires_at_raw is not None:
                try:
                    expires_at = datetime.fromisoformat(expires_at_raw)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    expires_at = None

                if expires_at is not None and expires_at < now:
                    # Deactivate expired ban
                    try:
                        await db.execute(
                            "UPDATE bans SET active = FALSE WHERE id = ?",
                            (row["id"],),
                        )
                        await db.commit()
                        logger.info(
                            "Boot-Restore: deactivated expired ban for %s "
                            "(expired at %s)",
                            ip,
                            expires_at_raw,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to deactivate expired ban id=%s: %s",
                            row["id"],
                            exc,
                        )
                    continue

            # Ban is still active — restore iptables rules
            await self._iptables_block(ip)
            restored += 1
            logger.info(
                "Boot-Restore: restored ban for %s (player=%s, reason=%s)",
                ip,
                row["player_name"],
                row["reason"],
            )

        logger.info("Boot-Restore complete: %d ban(s) restored", restored)
        return restored

    async def check_expired_bans(self) -> int:
        """Check for and process expired bans.

        Queries all active bans whose ``expires_at`` is in the past,
        removes their iptables rules, and marks them as inactive.

        Returns:
            Number of bans that were expired and removed.
        """
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        expired_count = 0

        try:
            cursor = await db.execute(
                "SELECT * FROM bans "
                "WHERE active = TRUE AND expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            logger.error("Failed to query expired bans: %s", exc)
            return 0

        for row in rows:
            ip = row["ip_address"]

            # Remove iptables rules
            await self._iptables_unblock(ip)

            # Mark ban as inactive
            try:
                await db.execute(
                    "UPDATE bans SET active = FALSE WHERE id = ?",
                    (row["id"],),
                )
                await db.commit()
                expired_count += 1
                logger.info(
                    "Ban expired: ip=%s, player=%s, reason=%s, expired_at=%s",
                    ip,
                    row["player_name"],
                    row["reason"],
                    row["expires_at"],
                )
            except Exception as exc:
                logger.error(
                    "Failed to deactivate expired ban id=%s: %s",
                    row["id"],
                    exc,
                )

        if expired_count:
            logger.info("Expired %d ban(s) in this cycle", expired_count)

        return expired_count

    async def add_ban(
        self,
        ip: str,
        player_name: str,
        server_type: str,
        reason: str,
        banned_by: str,
        duration_hours: int | None = None,
    ) -> bool:
        """Add a new ban.

        Args:
            ip: IP address to ban.
            player_name: In-game player name.
            server_type: Which server the ban originates from.
            reason: Human-readable reason for the ban.
            banned_by: Who or what issued the ban.
            duration_hours: Optional duration in hours.
                If ``None`` the ban is permanent.

        Returns:
            ``True`` if the ban was added successfully, ``False`` otherwise.
        """
        db = await get_db()
        now = datetime.now(timezone.utc)

        expires_at: str | None = None
        if duration_hours is not None:
            expires_at = (now + timedelta(hours=duration_hours)).isoformat()

        try:
            await db.execute(
                "INSERT INTO bans "
                "(ip_address, player_name, server_type, reason, banned_by, "
                " banned_at, expires_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, TRUE)",
                (
                    ip,
                    player_name,
                    server_type,
                    reason,
                    banned_by,
                    now.isoformat(),
                    expires_at,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.error("Failed to insert ban for %s: %s", ip, exc)
            return False

        # Apply iptables rules
        await self._iptables_block(ip)

        duration_str = f"{duration_hours}h" if duration_hours else "permanent"
        logger.info(
            "Ban added: ip=%s, player=%s, server=%s, reason=%s, by=%s, duration=%s",
            ip,
            player_name,
            server_type,
            reason,
            banned_by,
            duration_str,
        )
        return True

    async def remove_ban(self, ip: str) -> bool:
        """Remove (deactivate) all active bans for the given IP.

        Args:
            ip: The IP address to unban.

        Returns:
            ``True`` if at least one ban was deactivated, ``False`` otherwise.
        """
        db = await get_db()

        try:
            cursor = await db.execute(
                "UPDATE bans SET active = FALSE "
                "WHERE ip_address = ? AND active = TRUE",
                (ip,),
            )
            await db.commit()
            changed = cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to remove ban for %s: %s", ip, exc)
            return False

        if changed:
            await self._iptables_unblock(ip)
            logger.info("Ban removed for %s", ip)
        else:
            logger.warning("No active ban found for %s", ip)

        return changed

    async def get_active_bans(self) -> list[dict]:
        """Return all currently active bans.

        Returns:
            List of dicts, one per active ban row.
        """
        db = await get_db()

        try:
            cursor = await db.execute(
                "SELECT * FROM bans WHERE active = TRUE ORDER BY banned_at DESC"
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "ip_address": row["ip_address"],
                    "player_name": row["player_name"],
                    "server_type": row["server_type"],
                    "reason": row["reason"],
                    "banned_by": row["banned_by"],
                    "banned_at": row["banned_at"],
                    "expires_at": row["expires_at"],
                    "active": row["active"],
                    "source": row["source"],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.error("Failed to fetch active bans: %s", exc)
            return []
