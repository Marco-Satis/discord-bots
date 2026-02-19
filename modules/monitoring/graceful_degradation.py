"""
Graceful Degradation — Phase 10
Handles service failures without crashing the bot.
Tracks service health, retries, and notifies admins.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, List, Any
from enum import Enum

from utils.logger import get_logger

logger = get_logger("graceful_degradation")


class ServiceState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    RECOVERING = "recovering"


class ServiceHealth:
    """Tracks health of an individual service"""

    def __init__(self, name: str, retry_interval: int = 300) -> None:
        self.name: str = name
        self.state: ServiceState = ServiceState.HEALTHY
        self.retry_interval: int = retry_interval  # seconds
        self.last_failure: Optional[datetime] = None
        self.last_recovery: Optional[datetime] = None
        self.last_retry: Optional[datetime] = None
        self.failure_count: int = 0
        self.consecutive_failures: int = 0
        self.error_message: str = ""
        self._alerted: bool = False

    def mark_failure(self, error: str = "") -> None:
        """Record a service failure"""
        self.state = ServiceState.FAILED
        self.last_failure = datetime.now()
        self.failure_count += 1
        self.consecutive_failures += 1
        self.error_message = str(error)[:200]

    def mark_recovery(self) -> bool:
        """Record service recovery"""
        was_failed = self.state in (ServiceState.FAILED, ServiceState.DEGRADED)
        self.state = ServiceState.HEALTHY
        self.last_recovery = datetime.now()
        self.consecutive_failures = 0
        self.error_message = ""
        self._alerted = False
        return was_failed

    def should_retry(self) -> bool:
        """Check if enough time passed for a retry"""
        if self.state == ServiceState.HEALTHY:
            return True
        if not self.last_retry:
            return True
        elapsed = (datetime.now() - self.last_retry).total_seconds()
        # Exponential backoff: base * 2^(consecutive-1), capped at 30 min
        exponent = max(self.consecutive_failures - 1, 0)
        backoff = min(self.retry_interval * (2 ** min(exponent, 5)), 1800)
        return elapsed >= backoff

    def mark_retry(self) -> None:
        """Record a retry attempt"""
        self.last_retry = datetime.now()
        self.state = ServiceState.RECOVERING

    @property
    def needs_alert(self) -> bool:
        """Whether an alert should be sent (only once per failure)"""
        if self.state == ServiceState.FAILED and not self._alerted:
            self._alerted = True
            return True
        return False


class GracefulDegradation:
    """
    Manages service health and graceful degradation.
    
    Services register themselves, and the system tracks their health.
    When a service fails, the bot continues running with degraded functionality
    instead of crashing.
    """

    def __init__(self) -> None:
        self._services: Dict[str, ServiceHealth] = {}
        self.on_service_failed: Optional[Callable] = None
        self.on_service_recovered: Optional[Callable] = None

    def register(self, name: str, retry_interval: int = 300) -> ServiceHealth:
        """Register a service for health tracking"""
        svc = ServiceHealth(name, retry_interval)
        self._services[name] = svc
        return svc

    def get(self, name: str) -> Optional[ServiceHealth]:
        """Get service health by name"""
        return self._services.get(name)

    async def mark_failure(self, name: str, error: str = "") -> None:
        """Record a service failure"""
        svc = self._services.get(name)
        if not svc:
            svc = self.register(name)

        svc.mark_failure(error)
        logger.warning(f"Service degraded: {name} — {error}")

        if svc.needs_alert and self.on_service_failed:
            try:
                await self.on_service_failed(name, error)
            except Exception as e:
                logger.error(f"Failed callback error: {e}")

    async def mark_recovery(self, name: str) -> None:
        """Record a service recovery"""
        svc = self._services.get(name)
        if not svc:
            return

        was_failed = svc.mark_recovery()
        if was_failed:
            logger.info(f"Service recovered: {name}")
            if self.on_service_recovered:
                try:
                    await self.on_service_recovered(name)
                except Exception as e:
                    logger.error(f"Recovery callback error: {e}")

    def is_healthy(self, name: str) -> bool:
        """Check if a service is healthy"""
        svc = self._services.get(name)
        return svc is None or svc.state == ServiceState.HEALTHY

    def should_skip(self, name: str) -> bool:
        """Check if a service call should be skipped (failed + not time for retry)"""
        svc = self._services.get(name)
        if not svc or svc.state == ServiceState.HEALTHY:
            return False
        return not svc.should_retry()

    async def with_fallback(self, name: str, coro: Any, fallback: Optional[Any] = None) -> Any:
        """
        Execute a coroutine with automatic fallback on failure.
        
        Usage:
            result = await degradation.with_fallback(
                "onedrive",
                onedrive.upload(file),
                fallback=None
            )
        """
        svc = self._services.get(name)
        if not svc:
            svc = self.register(name)

        # Skip if service is down and not time for retry
        if svc.state != ServiceState.HEALTHY and not svc.should_retry():
            # Close the coroutine to prevent RuntimeWarning
            if hasattr(coro, 'close'):
                coro.close()
            return fallback

        svc.mark_retry()

        try:
            result = await coro
            await self.mark_recovery(name)
            return result
        except Exception as e:
            await self.mark_failure(name, str(e))
            return fallback

    def get_status(self) -> List[Dict[str, Any]]:
        """Get status of all tracked services"""
        result = []
        for name, svc in self._services.items():
            info = {
                "name": name,
                "state": svc.state.value,
                "failures": svc.failure_count,
                "consecutive": svc.consecutive_failures,
                "error": svc.error_message,
            }
            if svc.last_failure:
                info["last_failure"] = svc.last_failure.isoformat()
            if svc.last_recovery:
                info["last_recovery"] = svc.last_recovery.isoformat()
            result.append(info)
        return result

    def get_summary_text(self) -> str:
        """Get a human-readable status summary"""
        if not self._services:
            return "Keine Services registriert."

        lines = []
        for name, svc in self._services.items():
            icons = {
                ServiceState.HEALTHY: "✅",
                ServiceState.DEGRADED: "⚠️",
                ServiceState.FAILED: "❌",
                ServiceState.RECOVERING: "🔄",
            }
            icon = icons.get(svc.state, "❓")
            line = f"{icon} **{name}**: {svc.state.value}"
            if svc.error_message:
                line += f" — {svc.error_message}"
            lines.append(line)

        return "\n".join(lines)
