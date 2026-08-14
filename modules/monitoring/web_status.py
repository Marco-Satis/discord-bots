"""
Web-Status-Seite Generator — generiert statische HTML-Seite mit Server-Status

Wird alle 60 Sekunden vom Monitor Bot aufgerufen.
Schreibt eine HTML-Datei nach WEB_STATUS_PATH/index.html.
Erfordert Jinja2 (`pip install jinja2`).

ENV-Konfiguration:
    WEB_STATUS_ENABLED=true|false  (Standard: false)
    WEB_STATUS_PATH=/var/www/status (Standard)
"""

import asyncio
import functools
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

from jinja2 import Environment, FileSystemLoader

from utils.logger import get_logger
from utils.config import get_env

logger = get_logger("monitoring.web_status")

PROJECT_ROOT = Path(__file__).parent.parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "templates"


class WebStatusGenerator:
    """Generiert eine statische HTML-Status-Seite"""

    def __init__(self, output_path: str = "/var/www/status",
                 sat_server=None, mc_servers: dict = None,
                 health_checker=None, perf_monitor=None,
                 player_tracker=None):
        self.output_path = Path(output_path)
        self.output_file = self.output_path / "index.html"
        self.sat_server = sat_server
        self.mc_servers = mc_servers or {}
        self.health_checker = health_checker
        self.perf_monitor = perf_monitor
        self.player_tracker = player_tracker

        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        self._template = self._env.get_template("status.html")

    async def generate(self) -> bool:
        """Generiert die HTML-Datei. Gibt True bei Erfolg zurueck."""
        try:
            context = await self._collect_data()
            html = self._template.render(**context)

            # HTML-Datei schreiben via run_in_executor (async I/O)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, functools.partial(
                self._write_file, html
            ))

            return True
        except Exception as e:
            logger.error(f"Web-Status-Seite Fehler: {e}", exc_info=True)
            return False

    def _write_file(self, content: str) -> None:
        """Schreibt die HTML-Datei (synchron, fuer run_in_executor)"""
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.output_file.write_text(content, encoding="utf-8")

    async def _collect_data(self) -> Dict[str, Any]:
        """Sammelt alle Status-Daten fuer das Template"""
        now = datetime.now()
        context: Dict[str, Any] = {
            "updated_at": now.strftime("%d.%m.%Y %H:%M:%S"),
            "sat_online": False,
            "sat_players": 0,
            "mc_servers": [],
            "cpu_percent": 0,
            "ram_percent": 0,
            "disk_percent": 0,
        }

        # Satisfactory Status
        if self.sat_server:
            try:
                context["sat_online"] = await self.sat_server.is_running()
            except Exception:
                context["sat_online"] = False

            if context["sat_online"] and self.health_checker:
                try:
                    context["sat_players"] = self.health_checker.status.players_online
                except Exception as e:
                    logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        # Minecraft Server Status
        mc_list: List[Dict[str, Any]] = []
        for sid, srv in self.mc_servers.items():
            mc_info: Dict[str, Any] = {
                "name": srv.display_name,
                "online": False,
                "players": 0,
            }
            try:
                mc_info["online"] = await srv.is_running()
                if mc_info["online"]:
                    count, _ = await srv.get_player_count()
                    mc_info["players"] = count
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")
            mc_list.append(mc_info)
        context["mc_servers"] = mc_list

        # System Performance
        if self.perf_monitor:
            try:
                # get_current() gibt es am PerformanceMonitor nicht — der
                # Aufruf warf jedes Mal AttributeError, den das except
                # darunter verschluckt hat. Die Seite zeigte deshalb dauerhaft
                # 0 % fuer CPU, RAM und Disk.
                current = self.perf_monitor.get_latest()
                if current:
                    context["cpu_percent"] = round(current.cpu_percent, 1)
                    context["ram_percent"] = round(current.ram_percent, 1)
                    context["disk_percent"] = round(current.disk_percent, 1)
                    context["tick_rate"] = round(current.tick_rate, 1)
            except Exception as e:
                logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")

        return context
