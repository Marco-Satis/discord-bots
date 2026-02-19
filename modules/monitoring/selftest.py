"""
Self-Test Module — Phase 8b
Checks all systems and reports status.
"""

import asyncio
import psutil
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from utils.logger import get_logger
from utils.config import PROJECT_ROOT, get_env

logger = get_logger("selftest")


class SelfTest:
    """
    Comprehensive system check. Tests all bot subsystems and returns
    a structured report with pass/fail for each component.
    """

    def __init__(self, bot: Any) -> None:
        self.bot: Any = bot

    async def run_all(self) -> List[Dict[str, Any]]:
        """
        Run all self-tests and return list of results.
        Each result: {"name": str, "ok": bool, "detail": str, "category": str}
        """
        results = []

        # --- Discord ---
        results.append(await self._test_discord_channels())

        # --- Satisfactory API ---
        results.append(await self._test_api())

        # --- Server Process ---
        results.append(await self._test_server_process())

        # --- UFW / Sudo ---
        results.append(await self._test_ufw())

        # --- Disk Space ---
        results.append(await self._test_disk())

        # --- Savegame ---
        results.append(await self._test_savegame())

        # --- OneDrive ---
        results.append(await self._test_onedrive())

        # --- Email ---
        results.append(await self._test_email())

        # --- SteamCMD ---
        results.append(await self._test_steamcmd())

        # --- Config Files ---
        results.append(await self._test_config())

        # --- Minecraft Server ---
        mc_results = await self._test_minecraft()
        results.extend(mc_results)

        return results

    async def _test_discord_channels(self) -> Dict[str, Any]:
        """Test Discord channel access"""
        try:
            admin_ch_id = int(get_env("ADMIN_LOG_CHANNEL_ID", 0))
            status_ch_id = int(get_env("STATUS_EMBED_CHANNEL_ID", 0))
            game_ch_id = int(get_env("GAME_CHAT_CHANNEL_ID", 0))

            missing = []
            if admin_ch_id and not self.bot.get_channel(admin_ch_id):
                missing.append("Admin-Log")
            if status_ch_id and not self.bot.get_channel(status_ch_id):
                missing.append("Status-Embed")
            if game_ch_id and not self.bot.get_channel(game_ch_id):
                missing.append("Game-Chat")

            if missing:
                return {"name": "Discord Channels", "ok": False,
                        "detail": f"Nicht erreichbar: {', '.join(missing)}",
                        "category": "Discord"}

            return {"name": "Discord Channels", "ok": True,
                    "detail": "Alle Channels erreichbar", "category": "Discord"}
        except Exception as e:
            return {"name": "Discord Channels", "ok": False,
                    "detail": str(e)[:100], "category": "Discord"}

    async def _test_api(self) -> Dict[str, Any]:
        """Test Satisfactory API connection"""
        try:
            api = getattr(self.bot, "sat_api", None)
            if not api:
                return {"name": "Satisfactory API", "ok": False,
                        "detail": "API Client nicht konfiguriert", "category": "Server"}

            state = await api.query_server_state()
            if state:
                ver = getattr(state, "game_version", "?")
                return {"name": "Satisfactory API", "ok": True,
                        "detail": f"Verbunden (Version: {ver})", "category": "Server"}
            else:
                return {"name": "Satisfactory API", "ok": False,
                        "detail": "Keine Antwort", "category": "Server"}
        except Exception as e:
            return {"name": "Satisfactory API", "ok": False,
                    "detail": str(e)[:100], "category": "Server"}

    async def _test_server_process(self) -> Dict[str, Any]:
        """Test if server process is running"""
        try:
            server = getattr(self.bot, "sat_server", None)
            if not server:
                return {"name": "Server-Prozess", "ok": False,
                        "detail": "Server-Modul fehlt", "category": "Server"}

            running = await server.is_running()
            if running:
                status = await server.get_status()
                return {"name": "Server-Prozess", "ok": True,
                        "detail": f"PID {status.get('pid', '?')}, "
                                  f"CPU {status.get('cpu_percent', 0):.1f}%, "
                                  f"RAM {status.get('memory_mb', 0)} MB",
                        "category": "Server"}
            else:
                return {"name": "Server-Prozess", "ok": False,
                        "detail": "Nicht gestartet", "category": "Server"}
        except Exception as e:
            return {"name": "Server-Prozess", "ok": False,
                    "detail": str(e)[:100], "category": "Server"}

    async def _test_ufw(self) -> Dict[str, Any]:
        """Test UFW sudo access"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "ufw", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)

            if proc.returncode == 0:
                lines = stdout.decode().strip().split("\n")
                rule_count = max(0, len(lines) - 4)
                return {"name": "UFW Firewall", "ok": True,
                        "detail": f"Aktiv, {rule_count} Regeln", "category": "Sicherheit"}
            else:
                return {"name": "UFW Firewall", "ok": False,
                        "detail": f"sudo fehlgeschlagen: {stderr.decode()[:80]}",
                        "category": "Sicherheit"}
        except asyncio.TimeoutError:
            return {"name": "UFW Firewall", "ok": False,
                    "detail": "Timeout", "category": "Sicherheit"}
        except Exception as e:
            return {"name": "UFW Firewall", "ok": False,
                    "detail": str(e)[:100], "category": "Sicherheit"}

    async def _test_disk(self) -> Dict[str, Any]:
        """Test disk space"""
        try:
            disk = psutil.disk_usage("/")
            free_gb = disk.free / (1024 ** 3)
            pct = disk.percent

            ok = pct < 90
            detail = f"{pct}% belegt, {free_gb:.1f} GB frei"
            if pct >= 95:
                detail += " ⚠️ KRITISCH!"
            elif pct >= 90:
                detail += " ⚠️ Warnung"

            return {"name": "Festplatte", "ok": ok,
                    "detail": detail, "category": "System"}
        except Exception as e:
            return {"name": "Festplatte", "ok": False,
                    "detail": str(e)[:100], "category": "System"}

    async def _test_savegame(self) -> Dict[str, Any]:
        """Test savegame path and file accessibility"""
        try:
            save_path = Path(get_env(
                "SATISFACTORY_SAVE_PATH",
                "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"
            ))

            if not save_path.exists():
                return {"name": "Savegame", "ok": False,
                        "detail": f"Pfad nicht gefunden: {save_path}",
                        "category": "Server"}

            saves = list(save_path.rglob("*.sav"))
            if not saves:
                return {"name": "Savegame", "ok": False,
                        "detail": "Keine .sav Dateien gefunden",
                        "category": "Server"}

            # Find newest
            newest = max(saves, key=lambda p: p.stat().st_mtime)
            size_mb = newest.stat().st_size / (1024 * 1024)
            age_min = (datetime.now().timestamp() - newest.stat().st_mtime) / 60

            return {"name": "Savegame", "ok": True,
                    "detail": f"{len(saves)} Saves, neuestes: {size_mb:.1f} MB "
                              f"(vor {age_min:.0f} Min)",
                    "category": "Server"}
        except Exception as e:
            return {"name": "Savegame", "ok": False,
                    "detail": str(e)[:100], "category": "Server"}

    async def _test_onedrive(self) -> Dict[str, Any]:
        """Test OneDrive/rclone connectivity"""
        try:
            od = getattr(self.bot, "onedrive_backup", None)
            if not od or not od.enabled:
                return {"name": "OneDrive", "ok": True,
                        "detail": "Deaktiviert", "category": "Backup"}

            configured = await od.is_configured()
            if not configured:
                return {"name": "OneDrive", "ok": False,
                        "detail": "rclone nicht konfiguriert",
                        "category": "Backup"}

            return {"name": "OneDrive", "ok": True,
                    "detail": "Verbunden", "category": "Backup"}
        except Exception as e:
            return {"name": "OneDrive", "ok": False,
                    "detail": str(e)[:100], "category": "Backup"}

    async def _test_email(self) -> Dict[str, Any]:
        """Test email configuration"""
        try:
            em = getattr(self.bot, "email_notifier", None)
            if not em or not em.enabled:
                return {"name": "Email", "ok": True,
                        "detail": "Deaktiviert", "category": "Benachrichtigung"}

            if not em.smtp_host or not em.from_addr or not em.to_addr:
                return {"name": "Email", "ok": False,
                        "detail": "SMTP nicht vollstaendig konfiguriert",
                        "category": "Benachrichtigung"}

            return {"name": "Email", "ok": True,
                    "detail": f"Konfiguriert: {em.smtp_host}",
                    "category": "Benachrichtigung"}
        except Exception as e:
            return {"name": "Email", "ok": False,
                    "detail": str(e)[:100], "category": "Benachrichtigung"}

    async def _test_steamcmd(self) -> Dict[str, Any]:
        """Test SteamCMD availability"""
        try:
            uc = getattr(self.bot, "update_checker", None)
            if not uc:
                return {"name": "SteamCMD", "ok": False,
                        "detail": "Update Checker fehlt", "category": "System"}

            path = getattr(uc, "steamcmd_path", "/usr/games/steamcmd")
            if shutil.which(path) or Path(path).exists():
                return {"name": "SteamCMD", "ok": True,
                        "detail": f"Gefunden: {path}", "category": "System"}
            else:
                return {"name": "SteamCMD", "ok": False,
                        "detail": f"Nicht gefunden: {path}", "category": "System"}
        except Exception as e:
            return {"name": "SteamCMD", "ok": False,
                    "detail": str(e)[:100], "category": "System"}

    async def _test_config(self) -> Dict[str, Any]:
        """Test config files exist and are readable"""
        try:
            env_path = PROJECT_ROOT / "config" / ".env"
            cfg_path = PROJECT_ROOT / "config" / "config.json"

            issues = []
            if not env_path.exists():
                issues.append(".env fehlt")
            if not cfg_path.exists():
                issues.append("config.json fehlt")

            if issues:
                return {"name": "Konfiguration", "ok": False,
                        "detail": ", ".join(issues), "category": "System"}

            return {"name": "Konfiguration", "ok": True,
                    "detail": ".env + config.json OK", "category": "System"}
        except Exception as e:
            return {"name": "Konfiguration", "ok": False,
                    "detail": str(e)[:100], "category": "System"}

    async def _test_minecraft(self) -> List[Dict[str, Any]]:
        """Test Minecraft Server Subsysteme"""
        results = []
        mc_servers = getattr(self.bot, "mc_servers", {})

        if not mc_servers:
            return []  # Kein MC konfiguriert, keine Tests

        for sid, srv in mc_servers.items():
            # Server-Status
            try:
                running = await srv.is_running()
                status_text = "Online" if running else "Offline"
                results.append({
                    "name": f"MC {srv.display_name}",
                    "ok": True,
                    "detail": f"Service: {srv.service_name} — {status_text}",
                    "category": "Minecraft"
                })
            except Exception as e:
                results.append({
                    "name": f"MC {srv.display_name}",
                    "ok": False,
                    "detail": f"Status-Abfrage fehlgeschlagen: {e}",
                    "category": "Minecraft"
                })

            # RCON-Verbindung (nur wenn Server laeuft)
            if running and srv.rcon_password:
                try:
                    from modules.minecraft.rcon import MinecraftRCON
                    async with MinecraftRCON(
                        srv.rcon_host, srv.rcon_port,
                        srv.rcon_password, timeout=5.0
                    ) as rcon:
                        resp = await rcon.command("list")
                    results.append({
                        "name": f"MC {srv.display_name} RCON",
                        "ok": True,
                        "detail": f"Verbindung OK — {resp[:60]}",
                        "category": "Minecraft"
                    })
                except Exception as e:
                    results.append({
                        "name": f"MC {srv.display_name} RCON",
                        "ok": False,
                        "detail": f"RCON Fehler: {e}",
                        "category": "Minecraft"
                    })

            # Log-Pfad
            if srv.log_path:
                log_exists = srv.log_path.exists()
                results.append({
                    "name": f"MC {srv.display_name} Log",
                    "ok": log_exists,
                    "detail": (f"Gefunden: {srv.log_path}" if log_exists
                               else f"Nicht gefunden: {srv.log_path}"),
                    "category": "Minecraft"
                })

            # Backup-Pfad
            if srv.backup_path:
                bp_exists = srv.backup_path.exists()
                results.append({
                    "name": f"MC {srv.display_name} Backup",
                    "ok": bp_exists,
                    "detail": (f"Pfad OK: {srv.backup_path}" if bp_exists
                               else f"Nicht gefunden: {srv.backup_path}"),
                    "category": "Minecraft"
                })

        return results
