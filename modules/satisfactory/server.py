"""
Satisfactory Dedicated Server management via systemd
Netcup RS 4000 G12 - AMD EPYC 9645
"""

import asyncio
import time
import psutil
from pathlib import Path
from typing import Tuple, Optional, Dict, Any
from utils.logger import get_logger

logger = get_logger("satisfactory.server")

# Allowed systemctl actions (whitelist for security)
ALLOWED_ACTIONS = {"start", "stop", "restart", "status", "is-active"}


class SatisfactoryServer:
    """Manage Satisfactory Dedicated Server via systemd"""

    def __init__(self, service_name: str = "satisfactory.service",
                 server_user: str = "satisfactory",
                 server_path: str = "/home/satisfactory/SatisfactoryDedicatedServer") -> None:
        self.service_name = service_name
        self.server_user = server_user
        self.server_path = Path(server_path)
        self.save_path = (Path(f"/home/{server_user}") / ".config" / "Epic" /
                         "FactoryGame" / "Saved")

    # ------------------------------------------------------------------
    # systemctl helpers
    # ------------------------------------------------------------------

    async def _systemctl(self, action: str, timeout: int = 30, use_sudo: bool = True) -> Tuple[int, str, str]:
        """Run systemctl command asynchronously"""
        # Validate action against whitelist to prevent command injection
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Invalid systemctl action: {action}. Allowed: {ALLOWED_ACTIONS}")

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
            logger.error(f"systemctl {action} timed out ({timeout}s)")
            return -1, "", "Timeout"
        except Exception as e:
            logger.error(f"systemctl {action} error: {e}")
            return -1, "", str(e)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def is_running(self) -> bool:
        """Check if server is active via systemd (no sudo needed for read-only)"""
        code, stdout, _ = await self._systemctl("is-active", timeout=10, use_sudo=False)
        return code == 0 and stdout == "active"

    async def get_status(self) -> Dict[str, Any]:
        """Get comprehensive server status"""
        running = await self.is_running()
        info: Dict[str, Any] = {
            "running": running,
            "service": self.service_name,
            "uptime": 0,
            "cpu_percent": 0.0,
            "memory_mb": 0,
            "pid": None
        }
        if running:
            proc_info = await asyncio.get_running_loop().run_in_executor(
                None, self._find_process
            )
            if proc_info:
                info["pid"] = proc_info["pid"]
                info["uptime"] = proc_info["uptime"]
                info["cpu_percent"] = proc_info["cpu"]
                info["memory_mb"] = proc_info["mem_mb"]

            # Fallback: Wenn psutil keine Stats liefert (AccessDenied bei anderem User)
            # oder Prozess gar nicht gefunden wurde → /proc direkt lesen
            # OR statt AND: auch wenn nur CPU fehlt (AccessDenied) Fallback nutzen
            if info["cpu_percent"] == 0.0 or info["memory_mb"] == 0:
                fallback = await self._get_stats_from_systemd()
                if fallback:
                    info["pid"] = fallback["pid"]
                    info["uptime"] = fallback["uptime"]
                    info["cpu_percent"] = fallback["cpu"]
                    info["memory_mb"] = fallback["mem_mb"]
                    logger.debug(f"Prozess-Stats via /proc Fallback: PID={fallback['pid']}, "
                                 f"CPU={fallback['cpu']}%, RAM={fallback['mem_mb']}MB")
        return info

    def _find_process(self) -> Optional[Dict[str, Any]]:
        """Find FactoryServer process via psutil.
        Bevorzugt den echten Game-Prozess (FactoryServer-Linux-Shipping)
        statt dem Wrapper-Script (FactoryServer.sh)."""
        try:
            candidates = []
            for proc in psutil.process_iter(["name", "username", "pid",
                                              "create_time", "memory_info",
                                              "cmdline"]):
                try:
                    name = proc.info.get("name") or ""
                    user = proc.info.get("username") or ""
                    cmdline = proc.info.get("cmdline") or []
                    cmdline_str = " ".join(cmdline)

                    # Erweiterte Suche: Name ODER Cmdline enthaelt FactoryServer
                    is_factory = (
                        ("FactoryServer" in name and user == self.server_user)
                        or ("FactoryServer" in cmdline_str and user == self.server_user)
                        or ("Satisfactory" in cmdline_str and user == self.server_user)
                    )

                    if is_factory:
                        mem_info = proc.info.get("memory_info")
                        mem_mb = mem_info.rss // (1024 * 1024) if mem_info else 0
                        # Sammle alle Kandidaten, bevorzuge den mit meistem RAM
                        candidates.append((proc.info, mem_mb))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if not candidates:
                return None

            # Waehle den Prozess mit dem meisten RAM (= echtes Game, nicht Wrapper)
            best_info, best_mem = max(candidates, key=lambda x: x[1])

            # cpu_percent() braucht 2 Aufrufe fuer sinnvollen Wert
            try:
                p = psutil.Process(best_info["pid"])
                p.cpu_percent()  # Initialisierung (gibt 0 zurueck)
                time.sleep(0.1)
                cpu = p.cpu_percent()  # Jetzt echten Wert messen
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cpu = 0.0

            return {
                "pid": best_info["pid"],
                "uptime": int(time.time() - best_info["create_time"]),
                "cpu": round(cpu, 1),
                "mem_mb": best_mem,
            }
        except Exception as e:
            logger.error(f"Process lookup error: {e}")
        return None

    async def _get_stats_from_systemd(self) -> Optional[Dict[str, Any]]:
        """Fallback: PID von systemd holen und Stats aus /proc lesen.
        Funktioniert auch wenn psutil den Prozess nicht per Name findet."""
        import os
        try:
            # PID von systemctl holen (direkt, nicht ueber _systemctl wg. ALLOWED_ACTIONS)
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", "--property=MainPID", "--value", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            pid_str = stdout_bytes.decode("utf-8").strip()
            if proc.returncode != 0 or not pid_str:
                return None
            pid = int(pid_str)
            if pid <= 0:
                return None

            # RAM aus /proc/<pid>/status lesen (VmRSS)
            mem_mb = 0
            try:
                with open(f"/proc/{pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # VmRSS:   123456 kB
                            parts = line.split()
                            if len(parts) >= 2:
                                mem_mb = int(parts[1]) // 1024
                            break
            except (OSError, ValueError):
                pass

            # CPU + Uptime aus /proc/<pid>/stat lesen
            cpu = 0.0
            uptime_val = 0
            try:
                ticks_per_sec = os.sysconf("SC_CLK_TCK")
                with open(f"/proc/{pid}/stat", "r") as f:
                    stat_line = f.read()
                # Felder nach der letzten ')' aufteilen (Prozessname kann Klammern enthalten)
                parts = stat_line.split(")")[-1].split()
                utime = int(parts[11])   # user CPU time (Index 13 orig, 11 nach ')')
                stime = int(parts[12])   # system CPU time (Index 14 orig, 12 nach ')')
                starttime = int(parts[19])  # start time (Index 21 orig, 19 nach ')')

                with open("/proc/uptime", "r") as f:
                    system_uptime = float(f.read().split()[0])

                process_uptime = system_uptime - (starttime / ticks_per_sec)
                uptime_val = max(0, int(process_uptime))

                # CPU-Prozentsatz (Durchschnitt ueber gesamte Laufzeit)
                total_cpu_secs = (utime + stime) / ticks_per_sec
                if process_uptime > 0:
                    cpu = round((total_cpu_secs / process_uptime) * 100, 1)
            except (OSError, ValueError, IndexError):
                pass

            return {
                "pid": pid,
                "uptime": uptime_val,
                "cpu": cpu,
                "mem_mb": mem_mb,
            }
        except Exception as e:
            logger.debug(f"Systemd-Stats-Fallback fehlgeschlagen: {e}")
            return None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    async def start(self) -> Tuple[bool, str]:
        """Start server via systemd"""
        if await self.is_running():
            return False, "Server laeuft bereits."

        code, _, stderr = await self._systemctl("start", timeout=60)
        if code != 0:
            return False, f"Start fehlgeschlagen: {stderr}"

        # Wait for process to come up
        for _ in range(10):
            await asyncio.sleep(2)
            if await self.is_running():
                logger.info("Satisfactory server started")
                return True, "Server gestartet!"
        return False, "Start-Befehl gesendet, aber Server nicht aktiv."

    async def stop(self) -> Tuple[bool, str]:
        """Stop server via systemd"""
        if not await self.is_running():
            return False, "Server ist nicht gestartet."

        code, _, stderr = await self._systemctl("stop", timeout=90)
        if code != 0:
            return False, f"Stop fehlgeschlagen: {stderr}"

        # Wait for shutdown
        for _ in range(30):
            await asyncio.sleep(1)
            if not await self.is_running():
                logger.info("Satisfactory server stopped")
                return True, "Server gestoppt!"

        return False, "Stop-Befehl gesendet, Server reagiert nicht."

    async def restart(self, api: Optional[Any] = None,
                      save_wait: float = 4.0) -> Tuple[bool, str]:
        """Restart server via systemd.

        Wenn api uebergeben: triggert vor dem Restart ein SaveGame via
        Server-API und wartet save_wait Sekunden auf den Flush, damit das
        Save garantiert auf Platte ist bevor der Prozess neu startet.
        """
        # Pre-Restart-Save (verhindert Datenverlust)
        if api is not None and await self.is_running():
            try:
                saved = await api.save_game("pre_restart")
                if saved:
                    logger.info(
                        f"Pre-Restart-Save erfolgreich, warte {save_wait}s auf Flush"
                    )
                    await asyncio.sleep(save_wait)
                else:
                    logger.warning("Pre-Restart-Save fehlgeschlagen — Restart trotzdem")
            except Exception as e:
                logger.warning(f"Pre-Restart-Save Fehler: {e} — Restart trotzdem")

        code, _, stderr = await self._systemctl("restart", timeout=120)
        if code != 0:
            return False, f"Restart fehlgeschlagen: {stderr}"

        await asyncio.sleep(5)
        if await self.is_running():
            logger.info("Satisfactory server restarted")
            return True, "Server neugestartet!"
        return False, "Restart-Befehl gesendet, Server nicht aktiv."

    # ------------------------------------------------------------------
    # Savegame paths
    # ------------------------------------------------------------------

    @property
    def savegame_path(self) -> Path:
        return self.save_path / "SaveGames"

    @property
    def config_path(self) -> Path:
        return self.save_path / "Config" / "LinuxServer"

    @property
    def blueprint_path(self) -> Path:
        return self.save_path / "SaveGames" / "blueprints"
