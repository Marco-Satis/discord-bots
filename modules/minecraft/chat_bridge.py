"""
Minecraft Chat-Bridge: MC-Log → Discord und Discord → MC

MC → Discord:
  - Log-Polling (liest neue Zeilen aus latest.log)
  - Erkennt Chat, Join/Leave, Achievements, Deaths
  - Leitet Nachrichten an den konfigurierten Discord-Channel weiter

MC → Bot → MC (In-Game Befehle):
  - !status, !version, !players, !tps, !help (alle Spieler)
  - !cancel, !restart, !backup (nur OPs via ops.json)
  - Antworten via RCON /tellraw (formatiert) oder /say (einfach)

Discord → MC:
  - on_message Listener leitet Discord-Nachrichten via RCON an MC weiter
  - Rate-Limiting (max 1 RCON-Call pro Sekunde)

Pro Server wird eine eigene Instanz erstellt.
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Callable, Awaitable, List
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
    "Cave Spider": "Höhlenspinne",
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

# In-Game Befehle: <Spielername> !befehl [argumente]
COMMAND_RE = re.compile(
    rf'{_TS}\s+{_TH}<(\w+)>\s+!(\w+)(?:\s+(.*))?$',
    re.MULTILINE
)

# Server-Nachrichten (z.B. "Stopping the server")
SERVER_MSG_RE = re.compile(
    rf'{_TS}\s+{_TH}(?:Stopping|Starting|Done \(|Preparing)',
    re.MULTILINE
)


# M50-Fix: Mob-Ersetzungen einmalig vorkompilieren (vorher: sort + re.compile
# pro Aufruf, Death-Messages bei 5s-Poll). Laengste Namen zuerst, damit z.B.
# "Elder Guardian" vor "Guardian" und "Cave Spider" vor "Spider" greift.
_MOB_REPLACEMENTS: list = [
    (eng, re.compile(rf'\b{re.escape(eng)}\b'), de)
    for eng, de in sorted(MOB_DISPLAY_NAMES.items(), key=lambda x: len(x[0]), reverse=True)
    if eng != de
]


def translate_mob_names(message: str) -> str:
    """
    Ersetzt englische Mob-Namen in einer Nachricht durch deutsche Anzeigenamen.
    Wird auf Death-Messages angewandt fuer bessere Lesbarkeit.

    Nutzt vorkompilierte Word-Boundary-Patterns (`_MOB_REPLACEMENTS`), sortiert
    nach Laenge (laengste zuerst), damit z.B. "Elder Guardian" vor "Guardian"
    ersetzt wird.
    """
    result = message
    for eng_name, pattern, de_name in _MOB_REPLACEMENTS:
        if eng_name in result:  # billiger Pre-Check vor Regex
            result = pattern.sub(de_name, result)
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

        # Externe Referenzen (werden von recon_bot.py gesetzt)
        self.mc_server: Any = None       # MinecraftServer-Instanz
        self.update_manager: Any = None  # UpdateManager-Instanz

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

        # In-Game Befehle (!status, !help etc.)
        for match in COMMAND_RE.finditer(content):
            player = match.group(1)
            command = match.group(2).lower()
            args = (match.group(3) or "").strip()
            try:
                await self._handle_ingame_command(player, command, args)
            except Exception as e:
                logger.debug(f"[{self.server_id}] Befehl !{command} Fehler: {e}")

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
                # Zeitstempel und Prefix entfernen (Vanilla + NeoForge)
                death_msg = re.sub(
                    rf'^{_TS}\s+{_TH}', '', death_msg
                )
                # Mob-Namen uebersetzen fuer bessere Lesbarkeit
                death_msg = translate_mob_names(death_msg)
                try:
                    await self.on_death(self.server_id, player, death_msg)
                except Exception as e:
                    logger.debug(f"[{self.server_id}] on_death Fehler: {e}")

    # ------------------------------------------------------------------
    # In-Game Befehle (MC → Bot → RCON-Antwort)
    # ------------------------------------------------------------------

    async def _tellraw(self, components: List[Dict[str, str]]) -> None:
        """
        Sendet eine formatierte tellraw-Nachricht an alle Spieler via RCON.

        Args:
            components: Liste von Text-Komponenten (JSON-Format fuer tellraw)
        """
        if not self.mc_server:
            return
        payload = json.dumps(components, ensure_ascii=False)
        await self.mc_server.rcon_command(f"tellraw @a {payload}")

    async def _say(self, message: str) -> None:
        """Sendet eine einfache /say-Nachricht via RCON.

        INVARIANTE (Review 2026-06-12): NUR fuer interne/bot-generierte
        Strings — `message` wird unsanitisiert in `say {message}`
        interpoliert (im Gegensatz zur sanitisierten send_to_minecraft-
        Route). MC-Spielernamen ([A-Za-z0-9_]{3,16}) sind als Bestandteil
        ok; freie User-Inhalte vorher sanitizen (Newline/Quote/Selector).
        """
        if not self.mc_server:
            return
        await self.mc_server.rcon_command(f"say {message}")

    async def _is_op(self, player: str) -> bool:
        """
        Prueft ob Spieler OP-Rechte hat via ops.json (MC-Server-Datei).

        Args:
            player: Spielername (case-insensitive Vergleich)

        Returns:
            True wenn Spieler in ops.json eingetragen ist
        """
        if not self.mc_server:
            return False
        ops_file = self.mc_server.server_path / "ops.json"
        try:
            loop = asyncio.get_running_loop()

            def _read_ops() -> bool:
                if not ops_file.exists():
                    return False
                ops = json.loads(ops_file.read_text(encoding="utf-8"))
                return any(
                    op.get("name", "").lower() == player.lower() for op in ops
                )

            return await loop.run_in_executor(None, _read_ops)
        except Exception as e:
            logger.debug(f"[{self.server_id}] ops.json Lesefehler: {e}")
            return False

    async def _handle_ingame_command(
        self, player: str, command: str, args: str
    ) -> None:
        """
        Dispatcher fuer In-Game-Befehle.

        Args:
            player: Spielername des Befehlsgebers
            command: Befehlsname (ohne !, lowercase)
            args: Optionale Argumente
        """
        # Befehlstabelle: Name -> (Handler, braucht_op)
        commands: Dict[str, tuple] = {
            "status":  (self._cmd_status, False),
            "version": (self._cmd_version, False),
            "players": (self._cmd_players, False),
            "tps":     (self._cmd_tps, False),
            "help":    (self._cmd_help, False),
            "cancel":  (self._cmd_cancel, True),
            "restart": (self._cmd_restart, True),
            "backup":  (self._cmd_backup, True),
        }

        entry = commands.get(command)
        if not entry:
            return  # Unbekannter Befehl — ignorieren

        handler, needs_op = entry

        if needs_op:
            if not await self._is_op(player):
                await self._tellraw([
                    {"text": "[BOT] ", "color": "gold", "bold": True},
                    {"text": "Keine Berechtigung! Nur OPs können !",
                     "color": "red"},
                    {"text": command, "color": "red", "bold": True},
                    {"text": " ausführen.", "color": "red"},
                ])
                logger.info(
                    f"[{self.server_id}] !{command} von {player} abgelehnt (kein OP)"
                )
                return

        logger.info(f"[{self.server_id}] !{command} von {player}")
        await handler(player, args)

    # ------------------------------------------------------------------
    # Befehl-Handler
    # ------------------------------------------------------------------

    async def _cmd_status(self, player: str, args: str) -> None:
        """!status — Server-Status (Spieler, Uptime, TPS)"""
        if not self.mc_server:
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Server-Referenz nicht verfuegbar.", "color": "red"},
            ])
            return

        try:
            status = await self.mc_server.get_status()
            online = status.get("players_online", 0)
            max_p = status.get("players_max", 20)
            running = status.get("running", False)
            uptime_sec = status.get("uptime", 0)

            # Uptime formatieren
            if uptime_sec > 0:
                hours = uptime_sec // 3600
                minutes = (uptime_sec % 3600) // 60
                uptime_str = f"{hours}h {minutes}m"
            else:
                uptime_str = "unbekannt"

            status_text = "Online" if running else "Offline"
            status_color = "green" if running else "red"

            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Status: ", "color": "white"},
                {"text": status_text, "color": status_color, "bold": True},
                {"text": " | Spieler: ", "color": "white"},
                {"text": f"{online}/{max_p}", "color": "aqua"},
                {"text": " | Uptime: ", "color": "white"},
                {"text": uptime_str, "color": "yellow"},
            ])
        except Exception as e:
            logger.debug(f"[{self.server_id}] !status Fehler: {e}")
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Status konnte nicht abgerufen werden.", "color": "red"},
            ])

    async def _cmd_version(self, player: str, args: str) -> None:
        """!version — Aktuelle Modpack-Version + ob Update verfuegbar"""
        if not self.update_manager:
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Update-Manager nicht verfuegbar.", "color": "red"},
            ])
            return

        try:
            status = self.update_manager.get_status()
            current = status.get("current_version", "unbekannt")
            update_avail = status.get("update_available", False)
            latest = status.get("latest_version", "")

            components: List[Dict[str, str]] = [
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Version: ", "color": "white"},
                {"text": str(current), "color": "yellow", "bold": True},
            ]

            if update_avail and latest:
                components.extend([
                    {"text": " | Update verfuegbar: ", "color": "white"},
                    {"text": str(latest), "color": "green", "bold": True},
                ])
            else:
                components.append(
                    {"text": " (aktuell)", "color": "green"}
                )

            await self._tellraw(components)
        except Exception as e:
            logger.debug(f"[{self.server_id}] !version Fehler: {e}")
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Version konnte nicht abgerufen werden.", "color": "red"},
            ])

    async def _cmd_players(self, player: str, args: str) -> None:
        """!players — Spielerliste ALLER MC-Server"""
        if not self.mc_server:
            return

        try:
            # Eigenen Server abfragen
            players = await self.mc_server.get_players()
            online, max_p = await self.mc_server.get_player_count()

            components: List[Dict[str, str]] = [
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": f"{self.server_id}: ", "color": "white", "bold": True},
                {"text": f"{online}/{max_p}", "color": "aqua"},
            ]

            if players:
                player_list = ", ".join(sorted(players))
                components.extend([
                    {"text": " — ", "color": "gray"},
                    {"text": player_list, "color": "white"},
                ])
            else:
                components.append(
                    {"text": " (keine Spieler)", "color": "gray"}
                )

            await self._tellraw(components)
        except Exception as e:
            logger.debug(f"[{self.server_id}] !players Fehler: {e}")
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "Spielerliste nicht abrufbar.", "color": "red"},
            ])

    async def _cmd_tps(self, player: str, args: str) -> None:
        """!tps — Aktuelle Tick-Rate (Performance-Check)"""
        if not self.mc_server:
            return

        try:
            # NeoForge: "forge tps", Vanilla: nicht nativ verfuegbar
            response = await self.mc_server.rcon_command("forge tps")

            # Typische Antwort: "Dim 0 (overworld) : Mean tick time: 12.3 mspt. Mean TPS: 20.0"
            # Oder: "Overall : Mean tick time: 15.2 ms. Mean TPS: 20.0"
            tps_match = re.search(
                r'Overall\s*:.*?Mean TPS:\s*([\d.]+)', response
            )
            mspt_match = re.search(
                r'Overall\s*:.*?Mean tick time:\s*([\d.]+)', response
            )

            if tps_match:
                tps = float(tps_match.group(1))
                mspt = float(mspt_match.group(1)) if mspt_match else 0.0

                # Farbe je nach TPS
                if tps >= 19.5:
                    tps_color = "green"
                elif tps >= 15.0:
                    tps_color = "yellow"
                else:
                    tps_color = "red"

                components: List[Dict[str, str]] = [
                    {"text": "[BOT] ", "color": "gold", "bold": True},
                    {"text": "TPS: ", "color": "white"},
                    {"text": f"{tps:.1f}", "color": tps_color, "bold": True},
                    {"text": "/20", "color": "gray"},
                ]
                if mspt > 0:
                    components.extend([
                        {"text": " | MSPT: ", "color": "white"},
                        {"text": f"{mspt:.1f}ms", "color": tps_color},
                    ])

                await self._tellraw(components)
            else:
                # Fallback: Rohe Antwort kuerzen und anzeigen
                short = response[:150].replace('"', "'")
                await self._tellraw([
                    {"text": "[BOT] ", "color": "gold", "bold": True},
                    {"text": "TPS: ", "color": "white"},
                    {"text": short, "color": "yellow"},
                ])
        except Exception as e:
            logger.debug(f"[{self.server_id}] !tps Fehler: {e}")
            await self._tellraw([
                {"text": "[BOT] ", "color": "gold", "bold": True},
                {"text": "TPS nicht abrufbar (forge tps nicht verfuegbar?).",
                 "color": "red"},
            ])

    async def _cmd_cancel(self, player: str, args: str) -> None:
        """!cancel — Bricht laufendes Update/Countdown ab (OP-only)"""
        if not self.update_manager:
            await self._say("[BOT] Update-Manager nicht verfuegbar.")
            return

        try:
            cancelled = await self.update_manager.cancel()
            if cancelled:
                await self._say(
                    f"[BOT] Update-Countdown abgebrochen von {player}."
                )
                logger.info(
                    f"[{self.server_id}] Update abgebrochen von {player}"
                )
            else:
                await self._say("[BOT] Kein laufendes Update zum Abbrechen.")
        except Exception as e:
            logger.debug(f"[{self.server_id}] !cancel Fehler: {e}")
            await self._say("[BOT] Fehler beim Abbrechen.")

    async def _cmd_restart(self, player: str, args: str) -> None:
        """!restart — Server-Neustart mit 5min Countdown (OP-only)"""
        if not self.mc_server:
            return

        try:
            from modules.minecraft.mc_countdown import MCCountdownTimer

            # Pruefen ob bereits ein Countdown laeuft
            if (self.update_manager
                    and self.update_manager.get_status().get("timer_active")):
                await self._say(
                    "[BOT] Es laeuft bereits ein Countdown! "
                    "Nutze !cancel zum Abbrechen."
                )
                return

            await self._say(
                f"[BOT] Server-Neustart in 5 Minuten (von {player})."
            )
            logger.info(
                f"[{self.server_id}] Manueller Neustart von {player}"
            )

            timer = MCCountdownTimer(
                mc_server=self.mc_server,
                channel=None,
                extra_info=f"Manueller Neustart von {player}",
            )

            result = await timer.countdown(
                duration_minutes=5,
                action_name="Neustart",
            )

            if result.completed:
                await self.mc_server.rcon_command("stop")
            else:
                await self._say("[BOT] Neustart abgebrochen.")
        except Exception as e:
            logger.error(f"[{self.server_id}] !restart Fehler: {e}")
            await self._say("[BOT] Fehler beim Neustart-Countdown.")

    async def _cmd_backup(self, player: str, args: str) -> None:
        """!backup — Sofortiges World-Backup auslösen (OP-only)"""
        if not self.mc_server:
            return

        try:
            # Backup-Manager vom Bot holen
            backup_mgr = None
            if hasattr(self.mc_server, '_bot'):
                backup_mgr = getattr(
                    self.mc_server._bot, 'mc_backup_mgrs', {}
                ).get(self.server_id)

            # Fallback: UpdateManager hat evtl. backup_manager
            if not backup_mgr and self.update_manager:
                backup_mgr = getattr(
                    self.update_manager, 'backup_manager', None
                )

            if not backup_mgr:
                await self._say("[BOT] Backup-Manager nicht verfuegbar.")
                return

            await self._say(f"[BOT] Backup wird erstellt (von {player})...")
            logger.info(f"[{self.server_id}] Manuelles Backup von {player}")

            success, message, path = await backup_mgr.create_backup(
                created_by=player,
            )

            if success:
                await self._say(f"[BOT] Backup erstellt: {message}")
            else:
                await self._say(f"[BOT] Backup fehlgeschlagen: {message}")
        except Exception as e:
            logger.error(f"[{self.server_id}] !backup Fehler: {e}")
            await self._say("[BOT] Fehler beim Backup.")

    async def _cmd_help(self, player: str, args: str) -> None:
        """!help — Zeigt alle verfuegbaren In-Game-Befehle"""
        await self._tellraw([
            {"text": "[BOT] ", "color": "gold", "bold": True},
            {"text": "Verfuegbare Befehle:", "color": "white", "bold": True},
        ])
        await self._tellraw([
            {"text": "  !status", "color": "aqua"},
            {"text": " — Server-Status (Spieler, Uptime)", "color": "gray"},
        ])
        await self._tellraw([
            {"text": "  !version", "color": "aqua"},
            {"text": " — Modpack-Version + Update-Info", "color": "gray"},
        ])
        await self._tellraw([
            {"text": "  !players", "color": "aqua"},
            {"text": " — Online-Spieler", "color": "gray"},
        ])
        await self._tellraw([
            {"text": "  !tps", "color": "aqua"},
            {"text": " — Server-Performance (TPS/MSPT)", "color": "gray"},
        ])
        await self._tellraw([
            {"text": "  !cancel", "color": "yellow"},
            {"text": " — Update-Countdown abbrechen", "color": "gray"},
            {"text": " [OP]", "color": "red"},
        ])
        await self._tellraw([
            {"text": "  !restart", "color": "yellow"},
            {"text": " — Server-Neustart (5min)", "color": "gray"},
            {"text": " [OP]", "color": "red"},
        ])
        await self._tellraw([
            {"text": "  !backup", "color": "yellow"},
            {"text": " — World-Backup erstellen", "color": "gray"},
            {"text": " [OP]", "color": "red"},
        ])

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
