"""
Performance Monitor Module - System & Process Metrics
Tracks CPU, RAM, Disk usage with threshold warnings
"""

import psutil
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Dict, List, Any
from collections import deque

from utils.logger import get_logger
from modules.satisfactory.api_client import SAT_TICK_SOLL

logger = get_logger("performance")


@dataclass
class SystemMetrics:
    """Snapshot of system performance"""
    timestamp: datetime = field(default_factory=datetime.now)
    cpu_percent: float = 0.0
    cpu_count: int = 0
    ram_total_mb: int = 0
    ram_used_mb: int = 0
    ram_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0
    system_uptime: int = 0
    # Process-specific
    process_cpu: float = 0.0
    process_ram_mb: int = 0
    process_pid: Optional[int] = None
    # Tick-Rate des Spielservers. 0.0 heisst "kein Messwert" (Server offline
    # oder API nicht erreichbar) — das ist ausdruecklich KEINE gemessene Null
    # und loest deshalb keinen Alarm aus. Fuer den Offline-Fall gibt es die
    # Downtime-Meldung, die sonst doppelt feuern wuerde.
    tick_rate: float = 0.0


@dataclass
class PerformanceThresholds:
    """Warning thresholds for performance metrics"""
    cpu_warning: float = 80.0
    ram_warning: float = 85.0
    disk_warning: float = 90.0
    # Satisfactory laeuft mit 60 Ticks/s — siehe SAT_TICK_WARN in
    # modules/satisfactory/api_client.py. Der alte Wert 20 stammte aus einer
    # Zeit, in der ein 30er-Soll angenommen wurde, und haette selbst einen
    # auf ein Drittel eingebrochenen Server nicht gemeldet.
    tick_rate_warning: float = 50.0
    # Cooldown: don't spam warnings
    warning_cooldown: int = 300  # seconds


class PerformanceMonitor:
    """
    Monitors system and process performance.

    Usage:
        monitor = PerformanceMonitor(thresholds)
        metrics = await monitor.collect(server)
        warnings = monitor.check_thresholds(metrics)
    """

    def __init__(self, thresholds: Optional[PerformanceThresholds] = None,
                 history_size: int = 288) -> None:  # 288 = 24h at 5min intervals
        self.thresholds: PerformanceThresholds = thresholds or PerformanceThresholds()
        self.history: deque = deque(maxlen=history_size)
        self._last_warning_time: Dict[str, datetime] = {}

    async def collect(self, server: Optional[Any] = None,
                      tick_rate: Optional[float] = None) -> SystemMetrics:
        """
        Collect current system metrics.

        tick_rate: aktuelle Tick-Rate des Spielservers, falls der Aufrufer sie
        schon gemessen hat (der Health-Checker fragt sie ohnehin ab). Wird sie
        nicht uebergeben, bleibt sie 0.0 und die Schwelle greift nicht — ein
        Aufrufer, der sie nicht liefert, bekommt dadurch keinen Fehlalarm.
        """
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = psutil.boot_time()

        metrics = SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=cpu,
            cpu_count=psutil.cpu_count(),
            ram_total_mb=int(mem.total / 1024 / 1024),
            ram_used_mb=int(mem.used / 1024 / 1024),
            ram_percent=mem.percent,
            disk_total_gb=round(disk.total / 1024 / 1024 / 1024, 1),
            disk_used_gb=round(disk.used / 1024 / 1024 / 1024, 1),
            disk_percent=disk.percent,
            system_uptime=int(time.time() - boot),
            tick_rate=float(tick_rate or 0.0),
        )

        # Process-specific metrics
        if server:
            try:
                status = await server.get_status()
                if status.get("running"):
                    metrics.process_cpu = status.get("cpu_percent", 0)
                    metrics.process_ram_mb = status.get("memory_mb", 0)
                    metrics.process_pid = status.get("pid")
            except Exception as e:
                logger.debug(f"Process metrics error: {e}")

        self.history.append(metrics)
        return metrics

    def check_thresholds(self, metrics: SystemMetrics) -> List[str]:
        """Check metrics against thresholds, return list of warnings"""
        warnings = []
        now = datetime.now()
        cooldown = timedelta(seconds=self.thresholds.warning_cooldown)

        checks = [
            ("cpu", metrics.cpu_percent, self.thresholds.cpu_warning, "CPU"),
            ("ram", metrics.ram_percent, self.thresholds.ram_warning, "RAM"),
            ("disk", metrics.disk_percent, self.thresholds.disk_warning, "Disk"),
        ]

        for key, value, threshold, label in checks:
            if value > threshold:
                last = self._last_warning_time.get(key)
                if not last or (now - last) > cooldown:
                    warnings.append(
                        f"{label}: {value:.1f}% (Limit: {threshold:.0f}%)"
                    )
                    self._last_warning_time[key] = now

        # Tick-Rate ist die einzige Kennzahl hier, bei der ein NIEDRIGER Wert
        # das Problem ist — deshalb steht sie nicht in der Schleife oben.
        # Sie wurde bis 2026-08-14 ueberhaupt nicht geprueft: die Schwelle
        # stand in der Konfiguration, wurde im Dashboard angezeigt und hat nie
        # einen Alarm ausgeloest.
        #
        # 0.0 = kein Messwert (Server offline / API stumm). Das ist Sache der
        # Downtime-Meldung, hier bewusst kein Alarm.
        if 0.0 < metrics.tick_rate < self.thresholds.tick_rate_warning:
            last = self._last_warning_time.get("tick")
            if not last or (now - last) > cooldown:
                warnings.append(
                    f"Tick-Rate: {metrics.tick_rate:.1f} von {SAT_TICK_SOLL:.0f} "
                    f"(Limit: {self.thresholds.tick_rate_warning:.0f}) — "
                    f"der Server rechnet langsamer als er soll"
                )
                self._last_warning_time["tick"] = now

        return warnings

    def get_averages(self, minutes: int = 60) -> Dict[str, Any]:
        """Get average metrics over the last N minutes"""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        recent = [m for m in self.history if m.timestamp > cutoff]

        if not recent:
            return {"cpu": 0, "ram": 0, "disk": 0, "process_cpu": 0, "process_ram": 0}

        return {
            "cpu": round(sum(m.cpu_percent for m in recent) / len(recent), 1),
            "ram": round(sum(m.ram_percent for m in recent) / len(recent), 1),
            "disk": round(sum(m.disk_percent for m in recent) / len(recent), 1),
            "process_cpu": round(sum(m.process_cpu for m in recent) / len(recent), 1),
            "process_ram": round(sum(m.process_ram_mb for m in recent) / len(recent)),
            "samples": len(recent),
        }

    def get_peak(self, minutes: int = 60) -> Dict[str, Any]:
        """Get peak values over the last N minutes"""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        recent = [m for m in self.history if m.timestamp > cutoff]

        if not recent:
            return {"cpu": 0, "ram": 0, "disk": 0}

        return {
            "cpu": max(m.cpu_percent for m in recent),
            "ram": max(m.ram_percent for m in recent),
            "disk": max(m.disk_percent for m in recent),
        }

    def get_latest(self) -> Optional[SystemMetrics]:
        """Get most recent metrics"""
        return self.history[-1] if self.history else None

    def format_report(self, hours: int = 24) -> str:
        """Generate a text performance report"""
        avg = self.get_averages(minutes=hours * 60)
        peak = self.get_peak(minutes=hours * 60)
        latest = self.get_latest()

        lines = [f"Performance Report (letzte {hours}h)", ""]

        if latest:
            lines.append(f"**Aktuell:**")
            lines.append(
                f"CPU: {latest.cpu_percent:.1f}% | "
                f"RAM: {latest.ram_percent:.1f}% ({latest.ram_used_mb} MB) | "
                f"Disk: {latest.disk_percent:.1f}%"
            )
            if latest.tick_rate > 0:
                lines.append(
                    f"Tick-Rate: {latest.tick_rate:.1f} von {SAT_TICK_SOLL:.0f}"
                )
            if latest.process_pid:
                lines.append(
                    f"SAT Process: CPU {latest.process_cpu:.1f}% | "
                    f"RAM {latest.process_ram_mb} MB | PID {latest.process_pid}"
                )

        lines.append(f"\n**Durchschnitt ({avg['samples']} Samples):**")
        lines.append(
            f"CPU: {avg['cpu']:.1f}% | RAM: {avg['ram']:.1f}% | "
            f"Disk: {avg['disk']:.1f}%"
        )

        lines.append(f"\n**Spitzenwerte:**")
        lines.append(
            f"CPU: {peak['cpu']:.1f}% | RAM: {peak['ram']:.1f}% | "
            f"Disk: {peak['disk']:.1f}%"
        )

        return "\n".join(lines)
