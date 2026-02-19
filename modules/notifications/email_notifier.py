"""
Email Notifier Module - SMTP Email Notifications
Sends email alerts for critical server events (crashes, performance, failures)
"""

import smtplib
import asyncio
from html import escape as html_escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from utils.logger import get_logger

logger = get_logger("email_notifier")


class EmailNotifier:
    """
    Sends email notifications for critical events via SMTP.

    Usage:
        notifier = EmailNotifier(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="bot@example.com",
            password="app-password",
            from_addr="bot@example.com",
            to_addr="admin@example.com"
        )
        await notifier.send_crash_alert(crash_number=3)
    """

    def __init__(self, smtp_host: str, smtp_port: int = 587,
                 username: str = "", password: str = "",
                 from_addr: str = "", to_addr: str = "",
                 use_tls: bool = True, enabled: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.use_tls = use_tls
        self.enabled = enabled

        # Rate limiting: max 1 email per type per 15 minutes
        self._last_sent: Dict[str, datetime] = {}
        self._cooldown = timedelta(minutes=15)

    def _can_send(self, event_type: str) -> bool:  # type: ignore
        """Check if we can send (rate limiting)"""
        if not self.enabled:
            return False
        if not all([self.smtp_host, self.from_addr, self.to_addr]):
            return False

        last = self._last_sent.get(event_type)
        if last and (datetime.now() - last) < self._cooldown:
            logger.debug(f"Email rate limited for {event_type}")
            return False
        return True

    async def _send_email(self, subject: str, body: str, event_type: str) -> bool:  # type: ignore
        """Send an email via SMTP (runs in executor to not block)"""
        if not self._can_send(event_type):
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr
            msg["Subject"] = f"[SAT Server] {subject}"

            # Plain text body
            msg.attach(MIMEText(body, "plain", "utf-8"))

            # HTML body
            html_body = self._format_html(subject, body)
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            # Send in thread pool to not block event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._smtp_send, msg)

            self._last_sent[event_type] = datetime.now()
            logger.info(f"Email sent: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def _smtp_send(self, msg: MIMEMultipart) -> None:  # type: ignore
        """Blocking SMTP send (called in executor)"""
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
            if self.use_tls:
                server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(msg)

    def _format_html(self, subject: str, body: str) -> str:  # type: ignore
        """Format email as HTML"""
        safe_subject = html_escape(subject)
        safe_body = html_escape(body).replace("\n", "<br>")
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #1a1a2e; color: #e0e0e0; padding: 20px; border-radius: 8px;">
                <h2 style="color: #ff6b6b; margin-top: 0;">{safe_subject}</h2>
                <div style="background: #16213e; padding: 15px; border-radius: 5px; margin: 10px 0;">
                    {safe_body}
                </div>
                <hr style="border-color: #333;">
                <p style="color: #888; font-size: 12px;">
                    Satisfactory Server Monitor<br>
                    {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
                </p>
            </div>
        </body>
        </html>
        """

    # ------------------------------------------------------------------
    # Event-specific emails
    # ------------------------------------------------------------------

    async def send_crash_alert(self, crash_number: int,
                                auto_restart: bool = True) -> bool:  # type: ignore
        """Send crash notification email"""
        subject = f"🚨 Server Crash #{crash_number}"
        body = (
            f"Satisfactory Dedicated Server ist abgestuerzt!\n\n"
            f"Crash Nummer: {crash_number}\n"
            f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        )
        if auto_restart:
            body += "\nAuto-Restart wird ausgefuehrt..."
        else:
            body += "\nKein Auto-Restart konfiguriert. Manuelles Eingreifen erforderlich!"

        return await self._send_email(subject, body, "crash")

    async def send_restart_failed(self, crash_number: int, reason: str) -> bool:  # type: ignore
        """Send email when auto-restart fails"""
        subject = f"❌ Auto-Restart fehlgeschlagen (Crash #{crash_number})"
        body = (
            f"Der automatische Neustart nach Crash #{crash_number} ist fehlgeschlagen!\n\n"
            f"Grund: {reason}\n"
            f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
            f"MANUELLES EINGREIFEN ERFORDERLICH!"
        )
        return await self._send_email(subject, body, "restart_failed")

    async def send_performance_alert(self, warnings: List[str]) -> bool:  # type: ignore
        """Send email for critical performance warnings"""
        subject = "⚠️ Kritische Performance-Warnung"
        body = (
            f"Performance-Schwellwerte ueberschritten:\n\n"
            + "\n".join(f"• {w}" for w in warnings) +
            f"\n\nZeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
        return await self._send_email(subject, body, "performance")

    async def send_update_available(self, installed: str, available: str) -> bool:  # type: ignore
        """Send email about available update"""
        subject = "📦 Server Update verfuegbar"
        body = (
            f"Ein Update fuer den Satisfactory Dedicated Server ist verfuegbar.\n\n"
            f"Installierte Version: Build {installed}\n"
            f"Verfuegbare Version: Build {available}\n\n"
            f"Verwende /sat update im Discord um das Update durchzufuehren."
        )
        return await self._send_email(subject, body, "update")

    async def send_test(self) -> bool:  # type: ignore
        """Send a test email to verify configuration"""
        subject = "✅ Test-Email"
        body = (
            f"Dies ist eine Test-Email vom Satisfactory Server Monitor.\n\n"
            f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"Die Email-Konfiguration funktioniert korrekt!"
        )
        # Bypass rate limiting for test
        self._last_sent.pop("test", None)
        return await self._send_email(subject, body, "test")
