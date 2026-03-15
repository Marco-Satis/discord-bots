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
#
# Unterstuetzt BEIDE Log-Formate:
#   Vanilla:   [21:00:50] [Server thread/INFO]: Nachricht
#   NeoForge:  [12Maerz2026 21:00:50.857] [Server thread/INFO] [logger/]: Nachricht
#
# _TS matcht beliebige Zeitstempel-Klammer: \[[^\]]+\]
# _TH matcht Server-Thread + optionalen Logger-Tag:
#   [Server thread/INFO]: oder [Server thread/INFO] [logger/]:
# ------------------------------------------------------------------

_TS = r'\[[^\]]+\]'  # Zeitstempel (beliebig)
_TH = r'\[Server thread/INFO\](?:\s+\[[^\]]*\])*:\s+'  # Thread + optionale Logger-Tags

# Chat: <Spielername> Nachricht
CHAT_RE = re.compile(
    rf'{_TS}\s+{_TH}<(\w+)>\s+(.+)$',
    re.MULTILINE
)

# Join: Spielername joined the game
JOIN_RE = re.compile(
    rf'{_TS}\s+{_TH}(\w+) joined the game',
    re.MULTILINE
)

# Leave: Spielername left the game
LEAVE_RE = re.compile(
    rf'{_TS}\s+{_TH}(\w+) left the game',
    re.MULTILINE
)

# Achievements/Advancements: Spielername has made the advancement [Name]
ADVANCEMENT_RE = re.compile(
    rf'{_TS}\s+{_TH}(\w+) has (?:made the advancement|completed the challenge) \[(.+?)\]',
    re.MULTILINE
)

# Death messages: Diverse Formate, alle in [Server thread/INFO]
# Heuristik: Spielername + bekannte Death-Keywords
# Umfassende Liste aller Vanilla 1.20+ und gaengiger Modded Death-Messages
DEATH_KEYWORDS = [
    # --- Mob-bezogene Deaths ---
    "was slain by", "was shot by", "was killed by", "was blown up by",
    "was fireballed by", "was pummeled by", "was stung by",
    "was obliterated by", "was squashed by",
    # --- Fall-Deaths ---
    "fell from", "hit the ground", "fell off", "fell while",
    "fell out of the world", "didn't want to live in the same world as",
    "was doomed to fall by", "fell too far and was finished by",
    "was knocked into the void",
    # --- Feuer/Lava ---
    "went up in flames", "burned to death", "was burnt to a crisp",
    "tried to swim in lava", "walked into fire whilst fighting",
    "went off with a bang whilst fighting",
    # --- Ertrinken ---
    "drowned", "drowned whilst trying to escape",
    # --- Ersticken/Quetschen ---
    "suffocated", "suffocated in a wall", "was squished too much",
    "was squished", "was poked to death by a sweet berry bush",
    # --- Verhungern ---
    "starved to death",
    # --- Kaktus/Pflanzen ---
    "was pricked", "walked into a cactus", "was pricked to death",
    # --- Magie/Wither ---
    "withered away", "was killed by magic", "was killed by even more magic",
    # --- Explosion ---
    "blew up", "was killed by [Intentional Game Design]",
    # --- Blitz ---
    "was struck by lightning",
    # --- Eis/Freeze ---
    "was frozen", "froze to death",
    # --- Kinetik/Elytra ---
    "experienced kinetic energy",
    # --- Impale/Trident ---
    "was impaled", "was impaled by",
    "was impaled on a stalagmite",
    # --- Anvil/Stalactite ---
    "was squashed by a falling anvil",
    "was squashed by a falling block",
    "was skewered by a falling stalactite",
    # --- Generisch ---
    "died", "was killed", "died because of",
    # --- Warden ---
    "was obliterated by a sonically-charged shriek",
    # --- Modded Death-Keywords (Better MC, Forge, NeoForge) ---
    "was consumed by", "was devoured by", "was torn apart by",
    "was crushed by", "was incinerated by", "was electrocuted by",
    "was dissolved by", "was absorbed by", "was eradicated by",
    "was vaporized by", "was mauled by", "was trampled by",
    "was swarmed by", "was bitten by", "was clawed by",
    "was pecked to death by", "was drained by",
    "was haunted to death by", "was cursed by",
]

DEATH_PATTERN = "|".join(re.escape(kw) for kw in DEATH_KEYWORDS)
DEATH_RE = re.compile(
    rf'{_TS}\s+{_TH}(\w+) ({DEATH_PATTERN}).*$',
    re.MULTILINE
)

# ------------------------------------------------------------------
# Mob-Namen Uebersetzung (Interner Name → Anzeigename mit Emoji)
# Wird auf Death-Messages und Chat-Messages angewandt
# ------------------------------------------------------------------
MOB_DISPLAY_NAMES = {
    # --- Vanilla Hostile ---
    "Zombie": "Zombie",
    "Skeleton": "Skelett",
    "Creeper": "Creeper",
    "Spider": "Spinne",
    "Cave Spider": "Hoehlenspinne",
    "Enderman": "Enderman",
    "Endermite": "Endermite",
    "Witch": "Hexe",
    "Slime": "Schleim",
    "Magma Cube": "Magmawuerfel",
    "Blaze": "Lohe",
    "Ghast": "Ghast",
    "Wither Skeleton": "Witherskelett",
    "Wither": "Wither",
    "Ender Dragon": "Enderdrache",
    "Phantom": "Phantom",
    "Drowned": "Ertrunkener",
    "Husk": "Wuestenzombie",
    "Stray": "Eiswanderer",
    "Pillager": "Pluenderer",
    "Vindicator": "Diener",
    "Evoker": "Magier",
    "Vex": "Plagegeist",
    "Ravager": "Verwuester",
    "Guardian": "Waechter",
    "Elder Guardian": "Grosser Waechter",
    "Shulker": "Shulker",
    "Silverfish": "Silberfischchen",
    "Piglin": "Piglin",
    "Piglin Brute": "Piglin-Barbar",
    "Hoglin": "Hoglin",
    "Zoglin": "Zoglin",
    "Zombified Piglin": "Zombifizierter Piglin",
    "Warden": "Waechter des Deep Dark",
    "Breeze": "Brise",
    "Bogged": "Moorskelett",
    # --- Vanilla Neutral ---
    "Wolf": "Wolf",
    "Iron Golem": "Eisengolem",
    "Bee": "Biene",
    "Llama": "Lama",
    "Panda": "Panda",
    "Polar Bear": "Eisbaer",
    "Dolphin": "Delfin",
    "Goat": "Ziege",
    "Fox": "Fuchs",
    # --- Vanilla Passive ---
    "Chicken": "Huhn",
    "Cow": "Kuh",
    "Pig": "Schwein",
    "Sheep": "Schaf",
    "Horse": "Pferd",
    "Cat": "Katze",
    "Villager": "Dorfbewohner",
    "Wandering Trader": "Fahrender Haendler",
    # --- Modded Mobs (Better MC / beliebte Mods) ---
    "Creeperling": "Creeperling",
    "Lich": "Lich",
    "Naga": "Naga",
    "Hydra": "Hydra",
    "Minotaur": "Minotaurus",
    "Goblin": "Goblin",
    "Kobold": "Kobold",
    "Wraith": "Geist",
    "Banshee": "Banshee",
    "Ogre": "Oger",
    "Troll": "Troll",
    "Dark Knight": "Dunkler Ritter",
    "Ice Dragon": "Eisdrache",
    "Fire Dragon": "Feuerdrache",
    "Lightning Dragon": "Blitzdrache",
    "Sea Serpent": "Seeschlange",
    "Hippogryph": "Hippogreif",
    "Pixie": "Pixie",
    "Siren": "Sirene",
    "Cockatrice": "Basilisk",
    "Death Worm": "Todeswurm",
    "Myrmex": "Myrmex",
    "Amphithere": "Amphithere",
    "Cyclops": "Zyklop",
    "Gorgon": "Gorgone",
    "Grizzly Bear": "Grizzlybaer",
    "Flywheel": "Schwungrad",
}

# Server-Nachrichten (z.B. "Stopping the server")
SERVER_MSG_RE = re.compile(
    rf'{_TS}\s+{_TH}(?:Stopping|Starting|Done \(|Preparing)',
    re.MULTILINE
)


def translate_mob_names(message: str) -> str:
    """
    Ersetzt englische Mob-Namen in einer Nachricht durch deutsche Anzeigenamen.
    Wird auf Death-Messages angewandt fuer bessere Lesbarkeit.

    Verwendet Word-Boundaries und sortiert nach Laenge (laengste zuerst),
    damit z.B. "Elder Guardian" vor "Guardian" und "Cave Spider" vor "Spider"
    ersetzt wird.
    """
    result = message
    # Laengste Namen zuerst ersetzen, um Teilwort-Matches zu vermeiden
    for eng_name, de_name in sorted(MOB_DISPLAY_NAMES.items(), key=lambda x: len(x[0]), reverse=True):
        if eng_name in result and eng_name != de_name:
            # Word-Boundary regex statt einfachem replace
            result = re.sub(rf'\b{re.escape(eng_name)}\b', de_name, result)
    return result


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
                # Mob-Namen uebersetzen fuer bessere Lesbarkeit
                death_msg = translate_mob_names(death_msg)
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
