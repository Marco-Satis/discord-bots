"""
Login Audit Module - SSH Login Monitoring
Phase 4a: Monitors /var/log/auth.log for SSH logins from unknown IPs.
"""

import asyncio
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, List, Any

from utils.logger import get_logger

logger = get_logger("login_audit")

RE_SSH_LOGIN = re.compile(
    r"Accepted\s+(?:password|publickey)\s+for\s+(\S+)\s+from\s+(\d+\.\d+\.\d+\.\d+)"
)
RE_SSH_FAILED = re.compile(
    r"Failed\s+(?:password|publickey)\s+for\s+(?:invalid user\s+)?(\S+)\s+from\s+(\d+\.\d+\.\d+\.\d+)"
)


class LoginAudit:
    """
    Monitors /var/log/auth.log for SSH login events.
    Alerts on logins from unknown IPs.
    """

    AUTH_LOG = Path("/var/log/auth.log")

    def __init__(self, known_ips: Optional[List[str]] = None, data_dir: Optional[Path] = None) -> None:
        self.known_ips: set = set(known_ips or [])
        self._last_pos: int = 0
        self._last_size: int = 0
        self._recent_alerts: List[Dict[str, Any]] = []

        # Callbacks
        self.on_unknown_login: Optional[Callable] = None
        self.on_failed_login_burst: Optional[Callable] = None

        # Failed login tracking
        self._failed_counts: Dict[str, int] = {}

    def init_position(self) -> None:
        """Seek to end of auth.log to avoid replaying old events"""
        try:
            if self.AUTH_LOG.exists():
                self._last_size = self.AUTH_LOG.stat().st_size
                self._last_pos = self._last_size
                logger.info("Login audit initialized")
        except PermissionError:
            logger.warning("No permission to read auth.log — run as root or add botuser to adm group")
        except Exception as e:
            logger.error(f"Login audit init error: {e}")

    async def check(self) -> List[Dict[str, Any]]:
        """
        Read new auth.log entries and detect login events.
        Returns list of alert dicts.
        """
        alerts = []

        if not self.AUTH_LOG.exists():
            return alerts

        try:
            current_size = self.AUTH_LOG.stat().st_size

            # Log rotated?
            if current_size < self._last_size:
                self._last_pos = 0
            self._last_size = current_size

            if current_size <= self._last_pos:
                return alerts

            def _read():
                with open(self.AUTH_LOG, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(self._last_pos)
                    content = f.read()
                    return content, f.tell()

            content, new_pos = await asyncio.get_running_loop().run_in_executor(None, _read)
            self._last_pos = new_pos

            if not content:
                return alerts

            for line in content.splitlines():
                # Successful SSH login
                m = RE_SSH_LOGIN.search(line)
                if m:
                    user, ip = m.group(1), m.group(2)
                    if ip not in self.known_ips:
                        alert = {
                            "type": "unknown_login",
                            "user": user,
                            "ip": ip,
                            "timestamp": datetime.now().isoformat(),
                            "line": line.strip()[:200],
                        }
                        alerts.append(alert)
                        self._recent_alerts.append(alert)
                        logger.warning(f"Unknown SSH login: {user}@{ip}")

                        if self.on_unknown_login:
                            try:
                                await self.on_unknown_login(alert)
                            except Exception as e:
                                logger.error(f"Login callback error: {e}")

                # Failed SSH attempt
                m = RE_SSH_FAILED.search(line)
                if m:
                    user, ip = m.group(1), m.group(2)
                    self._failed_counts[ip] = self._failed_counts.get(ip, 0) + 1

                    # Alert on burst of failed logins (every 10 from same IP)
                    if self._failed_counts[ip] % 10 == 0:
                        alert = {
                            "type": "failed_burst",
                            "user": user,
                            "ip": ip,
                            "count": self._failed_counts[ip],
                            "timestamp": datetime.now().isoformat(),
                        }
                        alerts.append(alert)
                        logger.warning(f"Failed login burst: {ip} ({self._failed_counts[ip]} attempts)")

                        if self.on_failed_login_burst:
                            try:
                                await self.on_failed_login_burst(alert)
                            except Exception as e:
                                logger.error(f"Failed login callback error: {e}")

        except PermissionError:
            pass  # Silent — already warned at init
        except Exception as e:
            logger.debug(f"Login audit check error: {e}")

        # Trim recent alerts
        self._recent_alerts = self._recent_alerts[-50:]

        # Prevent memory leak: cap failed_counts dict
        if len(self._failed_counts) > 500:
            # Keep only the top 100 IPs by count
            sorted_ips = sorted(self._failed_counts.items(), key=lambda x: x[1], reverse=True)
            self._failed_counts = dict(sorted_ips[:100])

        return alerts

    def get_recent_alerts(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent login alerts"""
        return self._recent_alerts[-count:]

    def add_known_ip(self, ip: str) -> None:
        """Add an IP to the known/trusted list"""
        self.known_ips.add(ip)

    def remove_known_ip(self, ip: str) -> None:
        """Remove an IP from the known list"""
        self.known_ips.discard(ip)
