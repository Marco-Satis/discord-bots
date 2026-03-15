"""
Minecraft Dedicated Server Management via systemd
Unterstuetzt Multi-Server-Betrieb (z.B. Better MC + Vanilla/Paper)

Konfiguration per ENV-Variablen mit Server-ID-Prefix:
    MC_BMC_SERVICE, MC_BMC_PATH, MC_BMC_RCON_PORT, etc.
    MC_VANILLA_SERVICE, MC_VANILLA_PATH, MC_VANILLA_RCON_PORT, etc.
"""

import asyncio
from pathlib import Path
from typing import Tuple, Optional, Dict, List
from utils.logger import get_logger
from utils.config import get_env
from .rcon import MinecraftRCON

logger = get_logger("minecraft.server")


class MinecraftServer:
    """Minecraft Server-Verwaltung via systemd mit RCON-Support"""

    def __init__(self, server_id: str) -> None:
        """
        Erstellt eine Server-Instanz fuer den gegebenen Server.

        Args:
            server_id: Eindeutige Server-ID (z.B. "BMC", "VANILLA").
                       ENV-Variablen werden als MC_{server_id}_* gelesen.
        """
        self.server_id = server_id.upper()
        prefix = f"MC_{self.server_id}_"

        self.display_name = get_env(f"{prefix}DISPLAY_NAME", f"Minecraft {server_id}")
        self.service_name = get_env(f"{prefix}SERVICE", "")
        self.server_path = Path(get_env(f"{prefix}PATH", f"/home/minecraft/{server_id.lower()}"))
        self.rcon_host = get_env(f"{prefix}RCON_HOST", "127.0.0.1")
        try:
            self.rcon_port = int(get_env(f"{prefix}RCON_PORT", "25575"))
        except (ValueError, TypeError):
            self.rcon_port = 25575
            logger.warning(f"[{self.server_id}] Ungueltiger RCON_PORT, verwende Default 25575")
        self.rcon_password = get_env(f"{prefix}RCON_PASSWORD", "")
        self.world_path = Path(get_env(f"{prefix}WORLD_PATH", str(self.server_path / "world")))
        self.backup_path = Path(get_env(f"{prefix}BACKUP_PATH", f"/home/minecraft/backups/{server_id.lower()}"))
        self.log_path = Path(get_env(f"{prefix}LOG_PATH", str(self.server_path / "logs" / "latest.log")))
        try:
            self.game_chat_channel_id = int(get_env(f"{prefix}GAME_CHAT_CHANNEL_ID", "0"))
        except (ValueError, TypeError):
            self.game_chat_channel_id = 0
            logger.warning(f"[{self.server_id}] Ungueltige GAME_CHAT_CHANNEL_ID, verwende 0")

    @property
    def enabled(self) -> bool:
        """Server ist aktiviert wenn ein Service-Name konfiguriert ist"""
        return bool(self.service_name)

    # ------------------------------------------------------------------
    # systemctl Helfer
    # ------------------------------------------------------------------

    _ALLOWED_ACTIONS = frozenset({"start", "stop", "restart", "is-active", "status"})

    async def _systemctl(self, action: str, timeout: int = 30,
                         use_sudo: bool = True) -> Tuple[int, str, str]:
        """Fuehrt systemctl-Befehl asynchron aus"""
        if action not in self._ALLOWED_ACTIONS:
            return -1, "", f"Aktion nicht erlaubt: {action}"

        try:
            cmd = ["sudo", "/usr/bin/systemctl", action, self.service_name] if use_sudo \
                else ["/usr/bin/systemctl", action, self.service_name]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

        except asyncio.TimeoutError:
            logger.error(f"[{self.server_id}] systemctl {action} Timeout ({timeout}s)")
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return -1, "", "Timeout"
        except Exception as e:
            logger.error(f"[{self.server_id}] systemctl {action} Fehler: {e}")
            return -1, "", str(e)

    async def _get_main_pid(self) -> Optional[int]:
        """Liest die MainPID des Service aus systemd"""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", "--property=MainPID",
                "--value", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            pid = int(stdout.decode().strip())
            return pid if pid > 0 else None
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_running(self) -> bool:
        """Prueft ob der Server via systemd aktiv ist"""
        code, stdout, _ = await self._systemctl("is-active", timeout=10, use_sudo=False)
        return code == 0 and stdout == "active"

    async def get_status(self) -> Dict:
        """Umfassender Server-Status"""
        running = await self.is_running()
        info: Dict = {
            "server_id": self.server_id,
            "display_name": self.display_name,
            "running": running,
            "service": self.service_name,
            "uptime": 0,
            "pid": None,
            "players_online": 0,
            "players_max": 20,
        }

        if running:
            # PID und Uptime aus systemd
            pid = await self._get_main_pid()
            if pid:
                info["pid"] = pid
                # Uptime aus /proc/PID/stat (Startzeit)
                try:
                    proc_stat = Path(f"/proc/{pid}/stat")
                    if proc_stat.exists():
                        import time
                        boot_time = Path("/proc/stat").read_text()
                        # Einfachere Methode: systemctl show ActiveEnterTimestamp
                        proc = await asyncio.create_subprocess_exec(
                            "systemctl", "show", "--property=ActiveEnterTimestamp",
                            "--value", self.service_name,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                        ts_str = stdout.decode().strip()
                        if ts_str:
                            from datetime import datetime
                            # Format: "Wed 2026-02-19 12:11:58 CET"
                            # Einfacher: Sekunden seit Start berechnen
                            active_proc = await asyncio.create_subprocess_exec(
                                "systemctl", "show", "--property=ActiveEnterTimestampMonotonic",
                                "--value", self.service_name,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            mono_out, _ = await asyncio.wait_for(active_proc.communicate(), timeout=5)
                            mono_us = int(mono_out.decode().strip() or "0")
                            # Monotonic uptime berechnen
                            with open("/proc/uptime", "r") as f:
                                system_uptime = float(f.read().split()[0])
                            active_since_sec = mono_us / 1_000_000
                            info["uptime"] = max(0, int(system_uptime - active_since_sec))
                except Exception as e:
                    logger.debug(f"[{self.server_id}] Uptime-Berechnung fehlgeschlagen: {e}")

            # Spielerzahl via RCON
            try:
                players_online, players_max = await self.get_player_count()
                info["players_online"] = players_online
                info["players_max"] = players_max
            except Exception as e:
                logger.debug(f"[{self.server_id}] Spielerzahl nicht abrufbar: {e}")

        return info

    # ------------------------------------------------------------------
    # Steuerung
    # ------------------------------------------------------------------

    async def start(self) -> Tuple[bool, str]:
        """Server via systemd starten"""
        if await self.is_running():
            return False, f"{self.display_name} laeuft bereits."

        code, _, stderr = await self._systemctl("start", timeout=60)
        if code != 0:
            return False, f"Start fehlgeschlagen: {stderr}"

        # Warten bis Prozess aktiv ist
        for _ in range(15):
            await asyncio.sleep(2)
            if await self.is_running():
                logger.info(f"[{self.server_id}] Server gestartet")
                return True, f"{self.display_name} gestartet!"

        return False, "Start-Befehl gesendet, aber Server nicht aktiv."

    async def stop(self) -> Tuple[bool, str]:
        """Server via systemd stoppen"""
        if not await self.is_running():
            return False, f"{self.display_name} ist nicht gestartet."

        # In-Game-Warnung via RCON
        try:
            await self.rcon_command("say Der Server wird in Kuerze heruntergefahren.")
        except Exception as e:
            logger.debug(f"[{self.server_id}] In-Game-Warnung fehlgeschlagen: {e}")

        code, _, stderr = await self._systemctl("stop", timeout=90)
        if code != 0:
            return False, f"Stop fehlgeschlagen: {stderr}"

        # Warten auf Shutdown
        for _ in range(30):
            await asyncio.sleep(1)
            if not await self.is_running():
                logger.info(f"[{self.server_id}] Server gestoppt")
                return True, f"{self.display_name} gestoppt!"

        return False, "Stop-Befehl gesendet, Server reagiert nicht."

    async def restart(self) -> Tuple[bool, str]:
        """Server via systemd neustarten"""
        code, _, stderr = await self._systemctl("restart", timeout=120)
        if code != 0:
            return False, f"Restart fehlgeschlagen: {stderr}"

        await asyncio.sleep(5)
        if await self.is_running():
            logger.info(f"[{self.server_id}] Server neugestartet")
            return True, f"{self.display_name} neugestartet!"

        return False, "Restart-Befehl gesendet, Server nicht aktiv."

    # ------------------------------------------------------------------
    # RCON Commands
    # ------------------------------------------------------------------

    async def rcon_command(self, command: str) -> str:
        """
        RCON-Befehl auf dem Server ausfuehren.

        Args:
            command: RCON-Befehlsstring

        Returns:
            Antwort des Servers
        """
        if not self.rcon_password:
            raise RuntimeError(f"[{self.server_id}] RCON-Passwort nicht konfiguriert")

        if not await self.is_running():
            raise RuntimeError(f"[{self.server_id}] Server ist nicht gestartet")

        max_retries = 2
        last_error = None
        for attempt in range(max_retries):
            try:
                async with MinecraftRCON(
                    self.rcon_host,
                    self.rcon_port,
                    self.rcon_password,
                    timeout=10.0
                ) as rcon:
                    response = await rcon.command(command)
                    logger.debug(f"[{self.server_id}] RCON: {command} -> {response[:100]}")
                    return response
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    logger.debug(f"[{self.server_id}] RCON-Retry nach Fehler: {e}")
                    await asyncio.sleep(1)
                    continue
        logger.error(f"[{self.server_id}] RCON-Befehl fehlgeschlagen ({command}): {last_error}")
        raise last_error

    async def run_command(self, command: str) -> str:
        """
        Alias fuer rcon_command() — Kompatibilitaet mit RestartTimer.
        Der Timer erwartet api.run_command("say ...").
        """
        return await self.rcon_command(command)

    async def get_players(self) -> List[str]:
        """Liste der Online-Spieler via RCON"""
        try:
            response = await self.rcon_command("list")
            # Antwort: "There are X of max Y players online: player1, player2"
            if ":" in response:
                players_part = response.split(":")[-1].strip()
                if players_part:
                    return [p.strip() for p in players_part.split(",") if p.strip()]
            return []
        except Exception as e:
            logger.debug(f"[{self.server_id}] Spielerliste nicht abrufbar: {e}")
            return []

    async def get_player_count(self) -> Tuple[int, int]:
        """
        Online-Spielerzahl via RCON.

        Returns:
            (spieler_online, spieler_max)
        """
        try:
            response = await self.rcon_command("list")
            # Parse: "There are X of max Y players online"
            parts = response.lower().split(" of max ")
            if len(parts) >= 2:
                online = int(parts[0].split()[-1])
                max_players = int(parts[1].split()[0])
                return online, max_players
            return 0, 20
        except Exception as e:
            logger.debug(f"[{self.server_id}] Spielerzahl nicht parsbar: {e}")
            return 0, 20

    # ------------------------------------------------------------------
    # Server Properties
    # ------------------------------------------------------------------

    async def get_properties(self) -> Dict[str, str]:
        """server.properties auslesen (async via Executor)"""
        props_file = self.server_path / "server.properties"
        if not props_file.exists():
            logger.warning(f"[{self.server_id}] server.properties nicht gefunden: {props_file}")
            return {}

        def _read_props() -> Dict[str, str]:
            props: Dict[str, str] = {}
            with open(props_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, _, value = line.partition('=')
                        props[key.strip()] = value.strip()
            return props

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _read_props)
        except Exception as e:
            logger.error(f"[{self.server_id}] Fehler beim Lesen der server.properties: {e}")
            return {}

    async def set_property(self, key: str, value: str) -> bool:
        """Property in server.properties setzen (async via Executor)"""
        props_file = self.server_path / "server.properties"
        if not props_file.exists():
            logger.error(f"[{self.server_id}] server.properties nicht gefunden: {props_file}")
            return False

        def _write_prop() -> bool:
            lines = []
            found = False
            with open(props_file, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith('#') and '=' in stripped:
                        line_key = stripped.partition('=')[0].strip()
                        if line_key == key:
                            lines.append(f"{key}={value}\n")
                            found = True
                            continue
                    lines.append(line)

            if not found:
                lines.append(f"{key}={value}\n")

            with open(props_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return True

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _write_prop)
            if result:
                logger.info(f"[{self.server_id}] Property gesetzt: {key}={value}")
            return result
        except Exception as e:
            logger.error(f"[{self.server_id}] Fehler beim Setzen von {key}: {e}")
            return False

    # ------------------------------------------------------------------
    # World-Verwaltung
    # ------------------------------------------------------------------

    @property
    def world_save_path(self) -> Path:
        """Pfad zum World-Save-Verzeichnis"""
        return self.world_path

    async def get_world_size(self) -> int:
        """World-Ordner-Groesse in Bytes (async via Executor)"""
        def _calc() -> int:
            if not self.world_path.exists():
                return 0
            total = 0
            try:
                for entry in self.world_path.rglob("*"):
                    if entry.is_file():
                        total += entry.stat().st_size
            except Exception as e:
                logger.debug(f"[{self.server_id}] Fehler bei World-Groessenberechnung: {e}")
            return total

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _calc)
