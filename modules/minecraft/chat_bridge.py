"""
Minecraft Chat-Bridge: MC-Log → Discord und Discord → MC

MC → Discord:
  - Log-Polling (liest neue Zeilen aus latest.log)
  - Erkennt Chat, Join/Leave, Achievements, Deaths
  - Leitet Nachrichten an den konfigurierten Discord-Channel weiter

Discord → MC:
  - on_message Listener leitet Discord-Nachrichten via RCON an MC weiter
  - Rate-Limiting (max 1 RCON-Call pro Sekunde)

Pro Server wird eine eigene Instanz erstellt.
"""

import asyncio
import re
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable, List
from utils.logger import get_logger

logger = get_logger("minecraft.chat_bridge")

# ------------------------------------------------------------------
# Regex-Patterns fuer Minecraft Server-Logs
# Format: [HH:MM:SS] [Thread/Level]: Content
# ------------------------------------------------------------------

# Chat: <Spielername> Nachricht
CHAT_RE = re.compile(
    r'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+<(\w+)>\s+(.+)$',
    re.MULTILINE
)

# Join: Spielername joined the game
JOIN_RE = re.compile(
    r'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+(\w+) joined the game',
    re.MULTILINE
)

# Leave: Spielername left the game
LEAVE_RE = re.compile(
    r'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+(\w+) left the game',
    re.MULTILINE
)

# Achievements/Advancements: Spielername has made the advancement [Name]
ADVANCEMENT_RE = re.compile(
    r'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+(\w+) has (?:made the advancement|completed the challenge) \[(.+?)\]',
    re.MULTILINE
)

# Death messages: Diverse Formate, alle in [Server thread/INFO]
# Heuristik: Spielername + bekannte Death-Keywords
DEATH_KEYWORDS = [
    "was slain by", "was shot by", "was killed by", "was blown up by",
    "drowned", "fell from", "hit the ground", "went up in flames",
    "burned to death", "tried to swim in lava", "suffocated",
    "starved to death", "withered away", "was squished", "was pricked",
    "walked into a cactus", "was impaled", "was squashed",
    "experienced kinetic energy", "was frozen", "was struck by lightning",
    "died", "was fireballed by",
]

DEATH_PATTERN = "|".join(re.escape(kw) for kw in DEATH_KEYWORDS)
DEATH_RE = re.compile(
    rf'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+(\w+) ({DEATH_PATTERN})',
    re.MULTILINE
)

# Server-Nachrichten (z.B. "Stopping the server")
SERVER_MSG_RE = re.compile(
    r'\[[\d:]+\]\s+\[Server thread/INFO\]:\s+(?:Stopping|Starting|Done \(|Preparing)',
    re.MULTILINE
)


class MinecraftChatBridge:
    """
    Chat-Bridge fuer einen einzelnen Minecraft-Server.
    Ueberwacht das Server-Log und erkennt Events.
    """

    def __init__(
        self,
        server_id: str,
        log_path: Path,
        on_chat: Optional[Callable[[str, str, str], Awaitable]] = None,
        on_join: Optional[Callable[[str, str], Awaitable]] = None,
        on_leave: Optional[Callable[[str, str], Awaitable]] = None,
        on_advancement: Optional[Callable[[str, str, str], Awaitable]] = None,
        on_death: Optional[Callable[[str, str, str], Awaitable]] = None,
    ) -> None:
        """
        Args:
            server_id: Server-ID (z.B. "BMC")
            log_path: Pfad zur latest.log
            on_chat: Callback(server_id, player, message)
            on_join: Callback(server_id, player)
            on_leave: Callback(server_id, player)
            on_advancement: Callback(server_id, player, advancement)
            on_death: Callback(server_id, player, death_message)
        """
        self.server_id = server_id.upper()
        self.log_path = log_path
        self.on_chat = on_chat
        self.on_join = on_join
        self.on_leave = on_leave
        self.on_advancement = on_advancement
        self.on_death = on_death

        # Log-Position Tracking
        self._last_pos: int = 0
        self._last_size: int = 0
        self._initialized = False
        self._lock = asyncio.Lock()

        # Rate-Limiting fuer Discord → MC (RCON)
        self._last_rcon_time: float = 0.0
        self._rcon_min_interval: float = 1.0  # Sekunden

    async def initialize(self) -> None:
        """
        Log-Position auf aktuelles Dateiende setzen.
        Damit werden nur NEUE Zeilen nach Bot-Start verarbeitet.
        """
        if not self.log_path.exists():
            logger.debug(f"[{self.server_id}] Log nicht gefunden: {self.log_path}")
            self._initialized = True
            return

        loop = asyncio.get_running_loop()

        def _get_size() -> int:
            return self.log_path.stat().st_size

        try:
            size = await loop.run_in_executor(None, _get_size)
            self._last_pos = size
            self._last_size = size
            self._initialized = True
            logger.info(
                f"[{self.server_id}] Chat-Bridge initialisiert (Log-Position: {size})"
            )
        except Exception as e:
            logger.warning(f"[{self.server_id}] Chat-Bridge Init fehlgeschlagen: {e}")
            self._initialized = True

    async def poll(self) -> None:
        """
        Neue Log-Zeilen lesen und Events erkennen.
        Wird periodisch aufgerufen (z.B. alle 2-5 Sekunden).
        """
        if not self._initialized:
            await self.initialize()

        if not self.log_path.exists():
            return

        async with self._lock:
            try:
                loop = asyncio.get_running_loop()

                def _read_new() -> tuple[str, int, int]:
                    current_size = self.log_path.stat().st_size
                    last_pos = self._last_pos
                    last_size = self._last_size

                    # Log rotiert?
                    if current_size < last_size:
                        last_pos = 0

                    if current_size <= last_pos:
                        return "", last_pos, current_size

                    with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(last_pos)
                        content = f.read()
                        new_pos = f.tell()

                    return content, new_pos, current_size

                content, new_pos, new_size = await loop.run_in_executor(None, _read_new)
                self._last_pos = new_pos
                self._last_size = new_size

                if not content:
                    return

                # Events erkennen und Callbacks ausfuehren
                await self._process_log_content(content)

            except Exception as e:
                logger.debug(f"[{self.server_id}] Log-Poll Fehler: {e}")

    async def _process_log_content(self, content: str) -> None:
        """Log-Inhalt parsen und Callbacks ausfuehren"""

        # Chat-Nachrichten
        if self.on_chat:
            for match in CHAT_RE.finditer(content):
                player = match.group(1)
                message = match.group(2)
                try:
                    await self.on_chat(self.server_id, player, message)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_chat Fehler: {e}")

        # Joins
        if self.on_join:
            for match in JOIN_RE.finditer(content):
                player = match.group(1)
                try:
                    await self.on_join(self.server_id, player)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_join Fehler: {e}")

        # Leaves
        if self.on_leave:
            for match in LEAVE_RE.finditer(content):
                player = match.group(1)
                try:
                    await self.on_leave(self.server_id, player)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_leave Fehler: {e}")

        # Advancements
        if self.on_advancement:
            for match in ADVANCEMENT_RE.finditer(content):
                player = match.group(1)
                advancement = match.group(2)
                try:
                    await self.on_advancement(self.server_id, player, advancement)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_advancement Fehler: {e}")

        # Deaths
        if self.on_death:
            for match in DEATH_RE.finditer(content):
                player = match.group(1)
                death_msg = match.group(0)
                # Zeitstempel und Prefix entfernen
                death_msg = re.sub(
                    r'^\[[\d:]+\]\s+\[Server thread/INFO\]:\s+', '', death_msg
                )
                try:
                    await self.on_death(self.server_id, player, death_msg)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_death Fehler: {e}")

    # ------------------------------------------------------------------
    # Discord → MC (Rate-Limited RCON)
    # ------------------------------------------------------------------

    async def send_to_minecraft(
        self, server, author_name: str, message: str
    ) -> bool:
        """
        Discord-Nachricht via RCON an Minecraft senden.

        Args:
            server: MinecraftServer-Instanz
            author_name: Discord-Benutzername
            message: Nachricht

        Returns:
            True wenn erfolgreich
        """
        # Rate-Limiting
        now = time.monotonic()
        elapsed = now - self._last_rcon_time
        if elapsed < self._rcon_min_interval:
            await asyncio.sleep(self._rcon_min_interval - elapsed)

        self._last_rcon_time = time.monotonic()

        # Nachricht sanitisieren (keine RCON-Injection, Target-Selektoren, Zeilenumbrueche)
        safe_msg = (message.replace('"', "'").replace("\\", "")
                    .replace("\n", " ").replace("\r", ""))[:200]
        # MC Target-Selektoren escapen (@a, @p, @e, @r, @s)
        safe_msg = re.sub(r'@([apers])\b', r'@ \1', safe_msg)
        safe_name = (author_name.replace('"', "'")
                     .replace("\n", "").replace("\r", ""))[:20]
        safe_name = re.sub(r'@([apers])\b', r'@ \1', safe_name)

        try:
            # tellraw fuer farbige Nachrichten, Fallback auf say
            cmd = f'say [Discord] <{safe_name}> {safe_msg}'
            await server.rcon_command(cmd)
            return True
        except Exception as e:
            logger.debug(
                f"[{self.server_id}] Discord→MC Fehler: {e}"
            )
            return False

    @property
    def online_players(self) -> set[str]:
        """Aktuelle Online-Spieler (aus Join/Leave Events)"""
        # Wird vom Player-Tracker verwaltet, nicht hier
        return set()
