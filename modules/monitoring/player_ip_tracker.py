"""
Player IP Tracker - Tracks player IPs from server logs
and manages UFW-based IP bans for kick/ban functionality.

Persistenz: SQLite (player_ips, bans Tabellen) — alleinige Datenquelle
"""

import asyncio
import re
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

from utils.logger import get_logger
from utils.async_tasks import schedule_from_sync
from modules.database.db_manager import get_db

logger = get_logger("player_ip_tracker")


# IP validation pattern to prevent command injection
_IP_PATTERN = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')


def _validate_ip(ip: str) -> bool:  # Already typed
    """Validate IP address format to prevent command injection."""
    return bool(_IP_PATTERN.match(ip))

# ======================================================================
# Satisfactory Log-Regex Patterns
# ======================================================================

# Login request: ...RemoteAddr: 198.51.100.23:6786...?Name=SpielerName
RE_LOGIN = re.compile(
    r"Login request:.*RemoteAddr:\s*(\d+\.\d+\.\d+\.\d+):\d+.*\?Name=(\S+?)(?:\s|$|\?)"
)

# Alternative: AddClientConnection has IP, then Login request has Name
# We combine them via timing
RE_ADD_CONNECTION = re.compile(
    r"AddClientConnection:.*RemoteAddr:\s*(\d+\.\d+\.\d+\.\d+):(\d+)"
)

# Join succeeded: SpielerName
RE_JOIN = re.compile(r"Join succeeded:\s*(.+)")

# Login request line contains both IP and Name
RE_LOGIN_FULL = re.compile(
    r"Login request:.*\?Name=([^\s?]+).*userId:.*platform:"
)
RE_LOGIN_IP = re.compile(
    r"AddClientConnection:.*RemoteAddr:\s*(\d+\.\d+\.\d+\.\d+)"
)

# More reliable: parse Login request line which has both
RE_LOGIN_COMBINED = re.compile(
    r"Login request:\s+\?ClientIdentity=\S+.*\?Name=(\S+?)\??"
)

# ======================================================================
# Minecraft Log-Regex Patterns
# ======================================================================

# [Server thread/INFO]: PlayerName[/192.168.1.1:12345] logged in with entity id ...
RE_MC_LOGIN = re.compile(
    r"\[Server thread/INFO\].*?:\s*(\w+)\[/(\d+\.\d+\.\d+\.\d+):\d+\]\s+logged in"
)

# [Server thread/INFO]: PlayerName joined the game
RE_MC_JOIN = re.compile(
    r"\[Server thread/INFO\].*?:\s*(\w+) joined the game"
)

# [Server thread/INFO]: PlayerName left the game
RE_MC_LEFT = re.compile(
    r"\[Server thread/INFO\].*?:\s*(\w+) left the game"
)

# [User Authenticator #X/INFO]: UUID of player PlayerName is ...
RE_MC_UUID = re.compile(
    r"\[User Authenticator.*?/INFO\].*?UUID of player (\w+) is (\S+)"
)


class PlayerIPTracker:
    """
    Tracks player IPs from server logs (Satisfactory und Minecraft).
    Manages UFW firewall rules for IP-based bans.

    Persistenz: SQLite (player_ips, bans Tabellen) — alleinige Datenquelle.
    """

    def __init__(self, game_type: str = "sat",
                 game_ports: Optional[List[int]] = None,
                 **kwargs) -> None:
        """
        Args:
            game_type: "sat" fuer Satisfactory, "mc" fuer Minecraft
            game_ports: Server ports to block (default abhaengig von game_type)
        """
        self.game_type: str = game_type

        # Derive server_type for SQLite tables
        if game_type == "sat":
            self.server_type: str = "satisfactory"
        elif game_type == "mc":
            self.server_type = "minecraft"
        else:
            self.server_type = game_type

        if game_ports:
            self.game_ports: List[int] = game_ports
        elif game_type == "mc":
            self.game_ports = [25565]  # Standard MC Port
        else:
            self.game_ports = [7777, 7778, 8888, 8889]

        # ip_map: {player_name: {ip, last_seen, first_seen, uuid?}}
        # bans: {player_name: {ip, reason, banned_by, banned_at}}
        self._data: Dict[str, Any] = {"ip_map": {}, "bans": {}}

        # Pending connection IP (from AddClientConnection before Login) — SAT only
        self._pending_ip: Optional[str] = None

    async def load_from_db(self) -> None:
        """Load ip_map and bans from SQLite (alleinige Datenquelle)."""
        try:
            db = await get_db()

            # --- Load IP mappings ---
            cursor = await db.execute(
                """
                SELECT p.name, pi.ip_address, pi.first_seen, pi.last_seen
                FROM player_ips pi
                JOIN players p ON pi.player_id = p.id
                WHERE pi.server_type = ?
                """,
                (self.server_type,)
            )
            rows = await cursor.fetchall()

            if rows:
                db_ip_map: Dict[str, Any] = {}
                for row in rows:
                    name = row[0]  # p.name
                    ip = row[1]    # pi.ip_address
                    first_seen = row[2]  # pi.first_seen
                    last_seen = row[3]   # pi.last_seen
                    # Keep latest entry per player (rows may have multiple IPs)
                    existing = db_ip_map.get(name)
                    if not existing or (last_seen and last_seen > existing.get("last_seen", "")):
                        db_ip_map[name] = {
                            "ip": ip,
                            "first_seen": first_seen or "",
                            "last_seen": last_seen or "",
                        }
                self._data["ip_map"] = db_ip_map
                logger.info(
                    f"SQLite loaded: {len(db_ip_map)} player IP mappings "
                    f"for {self.server_type}"
                )
            else:
                logger.info(
                    f"Keine IP-Mappings in SQLite fuer {self.server_type} — starte leer"
                )

            # --- Load active bans ---
            cursor = await db.execute(
                """
                SELECT player_name, ip_address, reason, banned_by, banned_at
                FROM bans
                WHERE server_type = ? AND active = TRUE
                """,
                (self.server_type,)
            )
            ban_rows = await cursor.fetchall()

            if ban_rows:
                db_bans: Dict[str, Any] = {}
                for row in ban_rows:
                    player_name = row[0]  # player_name
                    if player_name:
                        db_bans[player_name] = {
                            "ip": row[1] or "?",      # ip_address
                            "reason": row[2] or "Kein Grund",  # reason
                            "banned_by": row[3] or "?",  # banned_by
                            "banned_at": row[4] or "?",  # banned_at
                        }
                self._data["bans"] = db_bans
                logger.info(
                    f"SQLite loaded: {len(db_bans)} active bans "
                    f"for {self.server_type}"
                )
            else:
                logger.info(
                    f"Keine Bans in SQLite fuer {self.server_type} — starte leer"
                )

        except Exception as e:
            logger.warning(
                f"SQLite-Lesefehler fuer {self.server_type}: {e}"
            )

    def _save(self) -> None:
        """No-op — alle Schreibvorgaenge gehen direkt nach SQLite."""
        pass

    def process_log_line(self, line: str) -> None:
        """
        Process a single log line to extract player IP mappings.
        Call this for each new log line during monitoring.
        Waehlt automatisch SAT- oder MC-Parser basierend auf game_type.
        """
        if self.game_type == "mc":
            self._process_mc_line(line)
        else:
            self._process_sat_line(line)

    def _process_sat_line(self, line: str) -> None:
        """Satisfactory-spezifisches Log-Parsing"""
        # Track pending IP from AddClientConnection
        m = RE_ADD_CONNECTION.search(line)
        if m:
            self._pending_ip = m.group(1)
            return

        # Login request contains Name - try to extract IP from same line
        if "Login request:" in line and "?Name=" in line:
            # Extract name
            name_match = re.search(r'\?Name=([^\s?]+)', line)
            # Extract IP from the connection context
            ip_match = re.search(r'RemoteAddr:\s*(\d+\.\d+\.\d+\.\d+)', line)

            if name_match:
                name = name_match.group(1)
                ip = None

                if ip_match:
                    ip = ip_match.group(1)
                elif self._pending_ip:
                    ip = self._pending_ip

                if ip and name:
                    self._update_mapping(name, ip)
                    self._pending_ip = None
                    return

        # Fallback: Join succeeded (use pending IP if available)
        m = RE_JOIN.search(line)
        if m and self._pending_ip:
            name = m.group(1).strip()
            self._update_mapping(name, self._pending_ip)
            self._pending_ip = None

    def _process_mc_line(self, line: str) -> None:
        """Minecraft-spezifisches Log-Parsing"""
        # PlayerName[/IP:Port] logged in
        m = RE_MC_LOGIN.search(line)
        if m:
            name = m.group(1)
            ip = m.group(2)
            self._update_mapping(name, ip)
            return

        # UUID of player PlayerName is UUID
        m = RE_MC_UUID.search(line)
        if m:
            name = m.group(1)
            uuid = m.group(2)
            # UUID im Mapping speichern falls Spieler bekannt
            existing = self._data["ip_map"].get(name)
            if existing:
                existing["uuid"] = uuid
                # No JSON save — UUID stored in memory only
                # (player_ips table does not have a uuid column)
            return

    def _update_mapping(self, name: str, ip: str) -> None:
        """Update IP mapping for a player. Writes to in-memory dict and fires
        off an async SQLite write."""
        now = datetime.now().isoformat()

        existing = self._data["ip_map"].get(name)
        if existing and existing.get("ip") == ip:
            # Same IP, just update last_seen
            existing["last_seen"] = now
        else:
            # New IP or new player
            self._data["ip_map"][name] = {
                "ip": ip,
                "first_seen": now,
                "last_seen": now,
            }
            logger.info(f"IP mapped: {name} -> {ip}")

        # Fire-and-forget async SQLite write from sync context
        self._fire_and_forget_db_write(name, ip, now)

    def _fire_and_forget_db_write(self, name: str, ip: str, now: str) -> None:
        """Schedule an async DB write from synchronous code.

        Uses utils.async_tasks.schedule_from_sync to hold a strong reference
        to the task — sonst kann der GC den Task verschlucken bevor der
        DSGVO-relevante IP-Write committed ist (audit 2026-05-17, async.md H2).
        """
        schedule_from_sync(
            self._db_update_mapping(name, ip, now),
            name=f"player_ip_tracker.db_update[{name}]",
        )

    async def _db_update_mapping(self, name: str, ip: str, now: str) -> None:
        """Write IP mapping to SQLite (player_ips + players tables)."""
        try:
            db = await get_db()

            # Ensure player exists in players table
            cursor = await db.execute(
                "SELECT id FROM players WHERE name = ?",
                (name,)
            )
            row = await cursor.fetchone()

            if row:
                player_id = row[0]
                # Update last_seen
                await db.execute(
                    "UPDATE players SET last_seen = ? WHERE id = ?",
                    (now, player_id)
                )
            else:
                # Insert new player
                cursor = await db.execute(
                    """
                    INSERT INTO players (name, first_seen, last_seen, server_type)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, now, now, self.server_type)
                )
                player_id = cursor.lastrowid

            # Check if this IP already exists for this player + server_type
            cursor = await db.execute(
                """
                SELECT id FROM player_ips
                WHERE player_id = ? AND ip_address = ? AND server_type = ?
                """,
                (player_id, ip, self.server_type)
            )
            ip_row = await cursor.fetchone()

            if ip_row:
                # Update last_seen
                await db.execute(
                    "UPDATE player_ips SET last_seen = ? WHERE id = ?",
                    (now, ip_row[0])
                )
            else:
                # Insert new IP mapping
                await db.execute(
                    """
                    INSERT INTO player_ips (player_id, ip_address, first_seen, last_seen, server_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (player_id, ip, now, now, self.server_type)
                )

            await db.commit()
        except Exception as e:
            logger.error(f"SQLite write failed for IP mapping {name}->{ip}: {e}")

    def get_ip(self, player_name: str) -> Optional[str]:
        """Get known IP for a player"""
        entry = self._data["ip_map"].get(player_name)
        return entry["ip"] if entry else None

    def get_player_by_ip(self, ip: str) -> Optional[str]:
        """Get player name by IP"""
        for name, entry in self._data["ip_map"].items():
            if entry.get("ip") == ip:
                return name
        return None

    def get_all_mappings(self) -> Dict[str, Any]:
        """Get all known IP mappings"""
        return dict(self._data["ip_map"])

    def get_all_bans(self) -> List[Dict[str, Any]]:
        """Get all active bans"""
        bans = []
        for name, info in self._data["bans"].items():
            bans.append({
                "name": name,
                "ip": info.get("ip", "?"),
                "reason": info.get("reason", "Kein Grund"),
                "banned_by": info.get("banned_by", "?"),
                "banned_at": info.get("banned_at", "?"),
            })
        return bans

    # ------------------------------------------------------------------
    # UFW Ban/Unban
    # ------------------------------------------------------------------

    async def ban_player(self, player_name: str, reason: str,
                         banned_by: str, api: Optional[Any] = None) -> Tuple[bool, str]:
        """
        Ban a player by blocking their IP via UFW.
        Phase 1b: SaveGame before ban to protect progress.
        Returns (success, message).
        """
        ip = self.get_ip(player_name)
        if not ip:
            return False, (
                f"Keine IP fuer **{player_name}** bekannt.\n"
                f"Der Spieler muss mindestens einmal verbunden gewesen sein."
            )

        # Phase 1b: Save game before ban
        save_ok = False
        if api:
            try:
                save_ok = await api.save_game()
                if save_ok:
                    await asyncio.sleep(2)  # Wait for save
                    logger.info(f"SaveGame before ban: OK")
            except Exception as e:
                logger.warning(f"SaveGame before ban failed: {e}")

        # Check if already banned
        if player_name in self._data["bans"]:
            return False, f"**{player_name}** ist bereits gebannt (IP: {ip})."

        # Apply UFW rules for all game ports
        success = await self._ufw_block(ip)
        if not success:
            return False, f"UFW-Regel konnte nicht gesetzt werden fuer {ip}."

        # Store ban in memory
        ban_time = datetime.now().isoformat()
        self._data["bans"][player_name] = {
            "ip": ip,
            "reason": reason,
            "banned_by": banned_by,
            "banned_at": ban_time,
        }

        # Write ban to SQLite
        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO bans (ip_address, player_name, server_type, reason, banned_by, banned_at, active, source)
                VALUES (?, ?, ?, ?, ?, ?, TRUE, 'tracker')
                """,
                (ip, player_name, self.server_type, reason, banned_by, ban_time)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"SQLite write failed for ban {player_name}: {e}")

        logger.info(f"Player banned: {player_name} (IP: {ip}) by {banned_by}")
        return True, (
            f"**{player_name}** wurde gebannt.\n"
            f"IP `{ip}` ist jetzt blockiert.\n"
            f"Grund: {reason}"
        )

    async def unban_player(self, player_name: str) -> Tuple[bool, str]:
        """
        Unban a player by removing UFW block.
        Returns (success, message).
        """
        ban_info = self._data["bans"].get(player_name)
        if not ban_info:
            return False, f"**{player_name}** ist nicht gebannt."

        ip = ban_info["ip"]

        # Remove UFW rules
        success = await self._ufw_unblock(ip)
        if not success:
            return False, f"UFW-Regel konnte nicht entfernt werden fuer {ip}."

        # Remove ban record from memory
        del self._data["bans"][player_name]

        # Deactivate ban in SQLite
        try:
            db = await get_db()
            await db.execute(
                """
                UPDATE bans SET active = FALSE
                WHERE player_name = ? AND server_type = ? AND active = TRUE
                """,
                (player_name, self.server_type)
            )
            await db.commit()
        except Exception as e:
            logger.error(f"SQLite write failed for unban {player_name}: {e}")

        logger.info(f"Player unbanned: {player_name} (IP: {ip})")
        return True, f"Ban fuer **{player_name}** aufgehoben (IP: `{ip}`)."

    async def kick_player(self, player_name: str, api: Optional[Any] = None) -> Tuple[bool, str]:
        """
        Kick a player by temporarily blocking their IP via UFW,
        then removing the block after a short delay.
        Phase 1b: SaveGame before kick to protect progress.
        Returns (success, message).
        """
        ip = self.get_ip(player_name)
        if not ip:
            return False, (
                f"Keine IP fuer **{player_name}** bekannt.\n"
                f"Der Spieler muss mindestens einmal verbunden gewesen sein."
            )

        # Phase 1b: Save game before kick
        save_ok = False
        if api:
            try:
                save_ok = await api.save_game()
                if save_ok:
                    await asyncio.sleep(2)  # Wait for save
                    logger.info(f"SaveGame before kick: OK")
            except Exception as e:
                logger.warning(f"SaveGame before kick failed: {e}")

        # Block IP
        success = await self._ufw_block(ip)
        if not success:
            return False, f"Kick fehlgeschlagen: UFW-Fehler fuer {ip}."

        logger.info(f"Player kicked: {player_name} (IP: {ip})")

        # Wait for connection to drop, then unblock
        # Only unblock if block succeeded to avoid race condition
        await asyncio.sleep(5)
        unblock_success = await self._ufw_unblock(ip)
        if not unblock_success:
            logger.warning(f"Failed to unblock IP after kick: {ip}")

        save_note = " (Spiel vorher gespeichert)" if save_ok else ""
        return True, f"**{player_name}** wurde gekickt (IP: `{ip}`).{save_note}"

    async def _ufw_block(self, ip: str) -> bool:  # Already typed
        """Block an IP via iptables REJECT (beide Richtungen).
        REJECT sendet ICMP unreachable - sofortige Trennung statt stilles DROP."""
        # Validate IP format to prevent command injection
        if not _validate_ip(ip):
            logger.error(f"Invalid IP format: {ip}")
            return False

        try:
            # 1. Eingehend: alle Pakete von dieser IP ablehnen (REJECT)
            proc_in = await asyncio.create_subprocess_exec(
                "sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "REJECT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_in, stderr_in = await asyncio.wait_for(
                proc_in.communicate(), timeout=10
            )

            if proc_in.returncode != 0:
                err = stderr_in.decode().strip()
                logger.error(f"iptables INPUT REJECT failed for {ip}: {err}")
                return False

            # 2. Ausgehend: alle Pakete zu dieser IP ablehnen (REJECT)
            proc_out = await asyncio.create_subprocess_exec(
                "sudo", "iptables", "-I", "OUTPUT", "-d", ip, "-j", "REJECT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_out, stderr_out = await asyncio.wait_for(
                proc_out.communicate(), timeout=10
            )

            if proc_out.returncode != 0:
                err = stderr_out.decode().strip()
                logger.warning(f"iptables OUTPUT REJECT failed for {ip}: {err}")
                # Eingehend ist schon blockiert, nicht abbrechen

            logger.info(f"iptables REJECT blocked: {ip} (INPUT + OUTPUT)")
            return True
        except Exception as e:
            logger.error(f"iptables block error: {e}")
            return False

    async def _ufw_unblock(self, ip: str) -> bool:  # Already typed
        """Remove iptables REJECT rules for an IP (beide Richtungen)."""
        # Validate IP format to prevent command injection
        if not _validate_ip(ip):
            logger.error(f"Invalid IP format: {ip}")
            return False

        try:
            # INPUT-Regel entfernen
            proc_in = await asyncio.create_subprocess_exec(
                "sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "REJECT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_in, stderr_in = await asyncio.wait_for(
                proc_in.communicate(), timeout=10
            )

            if proc_in.returncode == 0:
                logger.info(f"iptables INPUT unblocked: {ip}")
            else:
                err = stderr_in.decode().strip()
                if "No chain/target/match" in err or "does a matching rule" in err:
                    logger.warning(f"iptables INPUT rule not found for {ip}")
                else:
                    logger.error(f"iptables INPUT unblock failed for {ip}: {err}")

            # OUTPUT-Regel entfernen
            proc_out = await asyncio.create_subprocess_exec(
                "sudo", "iptables", "-D", "OUTPUT", "-d", ip, "-j", "REJECT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_out, stderr_out = await asyncio.wait_for(
                proc_out.communicate(), timeout=10
            )

            if proc_out.returncode == 0:
                logger.info(f"iptables OUTPUT unblocked: {ip}")
            else:
                err = stderr_out.decode().strip()
                if "No chain/target/match" in err or "does a matching rule" in err:
                    logger.warning(f"iptables OUTPUT rule not found for {ip}")
                    return True  # Already gone
                logger.error(f"UFW unblock failed for {ip}: {err}")
                return False
        except Exception as e:
            logger.error(f"UFW unblock error: {e}")
            return False

    async def sync_bans(self) -> None:
        """
        Ensure all stored bans are active in UFW.
        Call on bot startup to restore bans after reboot.
        Loads bans from SQLite bans table.
        """
        # Load bans from SQLite first
        try:
            db = await get_db()
            cursor = await db.execute(
                """
                SELECT player_name, ip_address, reason, banned_by, banned_at
                FROM bans
                WHERE server_type = ? AND active = TRUE
                """,
                (self.server_type,)
            )
            ban_rows = await cursor.fetchall()

            if ban_rows:
                db_bans: Dict[str, Any] = {}
                for row in ban_rows:
                    player_name = row[0]
                    if player_name:
                        db_bans[player_name] = {
                            "ip": row[1] or "?",
                            "reason": row[2] or "Kein Grund",
                            "banned_by": row[3] or "?",
                            "banned_at": row[4] or "?",
                        }
                self._data["bans"] = db_bans
                logger.info(
                    f"sync_bans: Loaded {len(db_bans)} active bans from SQLite "
                    f"for {self.server_type}"
                )
        except Exception as e:
            logger.warning(
                f"sync_bans: SQLite-Lesefehler, verwende In-Memory-Daten: {e}"
            )

        # Apply all bans to iptables
        for name, info in self._data["bans"].items():
            ip = info.get("ip")
            if ip:
                await self._ufw_block(ip)
                logger.debug(f"Ban synced: {name} ({ip})")
