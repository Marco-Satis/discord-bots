"""
Player IP Tracker - Tracks player IPs from server logs
and manages UFW-based IP bans for kick/ban functionality.
"""

import asyncio
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

from utils.logger import get_logger

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
    """

    def __init__(self, data_file: Path,
                 game_type: str = "sat",
                 game_ports: Optional[List[int]] = None) -> None:
        """
        Args:
            data_file: Path to JSON file storing IP mappings and bans
            game_type: "sat" fuer Satisfactory, "mc" fuer Minecraft
            game_ports: Server ports to block (default abhaengig von game_type)
        """
        self.data_file: Path = data_file
        self.game_type: str = game_type

        if game_ports:
            self.game_ports: List[int] = game_ports
        elif game_type == "mc":
            self.game_ports = [25565]  # Standard MC Port
        else:
            self.game_ports = [7777, 7778, 8888, 8889]

        # ip_map: {player_name: {ip, last_seen, first_seen, uuid?}}
        # bans: {player_name: {ip, reason, banned_by, banned_at}}
        self._data: Dict[str, Any] = {"ip_map": {}, "bans": {}}
        self._load()

        # Pending connection IP (from AddClientConnection before Login) — SAT only
        self._pending_ip: Optional[str] = None

    def _load(self) -> None:
        """Load data from file"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r') as f:
                    self._data = json.load(f)
                # Ensure keys exist
                self._data.setdefault("ip_map", {})
                self._data.setdefault("bans", {})
                logger.info(
                    f"Loaded {len(self._data['ip_map'])} player IPs, "
                    f"{len(self._data['bans'])} bans"
                )
        except Exception as e:
            logger.error(f"Failed to load IP data: {e}")

    def _save(self) -> None:
        """Save data to file"""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_file, 'w') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save IP data: {e}")

    def process_log_line(self, line: str) -> None:
        """
        Process a single log line to extract player IP mappings.
        Call this for each new log line during monitoring.
        Wählt automatisch SAT- oder MC-Parser basierend auf game_type.
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
                self._save()
            return

    def _update_mapping(self, name: str, ip: str) -> None:
        """Update IP mapping for a player"""
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

        self._save()

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
                f"Keine IP für **{player_name}** bekannt.\n"
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
            return False, f"UFW-Regel konnte nicht gesetzt werden für {ip}."

        # Store ban
        self._data["bans"][player_name] = {
            "ip": ip,
            "reason": reason,
            "banned_by": banned_by,
            "banned_at": datetime.now().isoformat(),
        }
        self._save()

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
            return False, f"UFW-Regel konnte nicht entfernt werden für {ip}."

        # Remove ban record
        del self._data["bans"][player_name]
        self._save()

        logger.info(f"Player unbanned: {player_name} (IP: {ip})")
        return True, f"Ban für **{player_name}** aufgehoben (IP: `{ip}`)."

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
                f"Keine IP für **{player_name}** bekannt.\n"
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
            return False, f"Kick fehlgeschlagen: UFW-Fehler für {ip}."

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
        """Block an IP via UFW for all game ports"""
        # Validate IP format to prevent command injection
        if not _validate_ip(ip):
            logger.error(f"Invalid IP format: {ip}")
            return False

        try:
            # Insert deny rule before any allow rules (priority)
            # Use create_subprocess_exec with argument list instead of shell
            proc = await asyncio.create_subprocess_exec(
                "sudo", "ufw", "insert", "1", "deny", "from", ip, "to", "any",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10
            )

            if proc.returncode == 0:
                logger.info(f"UFW blocked: {ip}")
                return True
            else:
                err = stderr.decode().strip()
                # Already exists is fine
                if "Skipping" in err or "already exists" in (stdout.decode() + err):
                    logger.info(f"UFW rule already exists for {ip}")
                    return True
                logger.error(f"UFW block failed for {ip}: {err}")
                return False
        except Exception as e:
            logger.error(f"UFW block error: {e}")
            return False

    async def _ufw_unblock(self, ip: str) -> bool:  # Already typed
        """Remove UFW block for an IP"""
        # Validate IP format to prevent command injection
        if not _validate_ip(ip):
            logger.error(f"Invalid IP format: {ip}")
            return False

        try:
            # Use create_subprocess_exec with argument list instead of shell
            proc = await asyncio.create_subprocess_exec(
                "sudo", "ufw", "delete", "deny", "from", ip, "to", "any",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10
            )

            if proc.returncode == 0:
                logger.info(f"UFW unblocked: {ip}")
                return True
            else:
                err = stderr.decode().strip()
                if "Could not delete" in (stdout.decode() + err):
                    logger.warning(f"UFW rule not found for {ip}")
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
        """
        for name, info in self._data["bans"].items():
            ip = info.get("ip")
            if ip:
                await self._ufw_block(ip)
                logger.debug(f"Ban synced: {name} ({ip})")
