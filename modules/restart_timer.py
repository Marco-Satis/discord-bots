"""
Restart Timer - Reusable Countdown System
Provides configurable countdowns with in-game + Discord warnings.
Used by: manual restart, manual stop, blueprint restart, daily restart (monitor bot)

Usage:
    timer = RestartTimer(api, channel)
    result = await timer.countdown(
        duration_minutes=10,
        action_name="Restart",
        warnings=[10, 5, 3, 1]
    )
    if result == TimerResult.COMPLETED:
        # Do the action
    elif result == TimerResult.CANCELLED:
        # Was cancelled
"""

import asyncio
import enum
from typing import Optional, List, Callable, Awaitable
import discord
from utils.logger import get_logger

logger = get_logger("restart_timer")


class TimerResult(enum.Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class RestartTimer:
    """
    Reusable countdown timer with Discord + in-game warnings.
    
    Features:
    - Configurable warning intervals (e.g. 10, 5, 3, 1 min)
    - Cancel support via cancel() method
    - In-game messages via Satisfactory API
    - Discord channel messages
    - Progress updates
    """

    def __init__(self, api=None, channel: Optional[discord.TextChannel] = None) -> None:
        """
        Args:
            api: SatisfactoryAPI instance for in-game messages (optional)
            channel: Discord TextChannel for countdown messages (optional)
        """
        self.api = api
        self.channel = channel
        self._cancel_event = asyncio.Event()
        self._active = False
        self._action_name = ""

    @property
    def is_active(self) -> bool:  # type: ignore
        return self._active

    @property
    def action_name(self) -> str:  # type: ignore
        return self._action_name

    def cancel(self) -> None:  # type: ignore
        """Cancel the running countdown"""
        if self._active:
            self._cancel_event.set()
            logger.info(f"Timer cancelled: {self._action_name}")

    async def countdown(
        self,
        duration_minutes: int = 10,
        action_name: str = "Restart",
        warnings: List[int] = None,
        on_complete: Optional[Callable[[], Awaitable]] = None,
    ) -> TimerResult:
        """
        Run a countdown with warnings.
        
        Args:
            duration_minutes: Total countdown duration
            warnings: List of minute marks to send warnings (default: [10, 5, 3, 1])
            action_name: Name for messages (e.g. "Restart", "Stop", "Update")
            on_complete: Optional async callback when timer completes
            
        Returns:
            TimerResult.COMPLETED if timer finished
            TimerResult.CANCELLED if cancelled
            TimerResult.ERROR if an error occurred
        """
        if self._active:
            logger.warning("Timer already active, ignoring new request")
            return TimerResult.ERROR

        if warnings is None:
            # Default warning schedule based on duration
            if duration_minutes >= 10:
                warnings = [10, 5, 3, 1]
            elif duration_minutes >= 5:
                warnings = [5, 3, 1]
            elif duration_minutes >= 3:
                warnings = [3, 1]
            else:
                warnings = [duration_minutes]

        # Filter warnings to only include those within our duration
        warnings = sorted([w for w in warnings if w <= duration_minutes], reverse=True)

        self._active = True
        self._action_name = action_name
        self._cancel_event.clear()

        logger.info(f"Timer started: {action_name} in {duration_minutes}min (warnings: {warnings})")

        try:
            total_seconds = duration_minutes * 60
            elapsed = 0

            # Send initial message
            await self._send_warning(
                f"Server-{action_name} in {duration_minutes} Minuten!",
                is_initial=True
            )

            # Process each warning interval
            for i, warning_min in enumerate(warnings):
                # Calculate how long to wait until this warning
                warning_at_seconds = total_seconds - (warning_min * 60)
                wait_seconds = warning_at_seconds - elapsed

                if wait_seconds > 0:
                    cancelled = await self._wait_or_cancel(wait_seconds)
                    if cancelled:
                        await self._send_cancel_message()
                        return TimerResult.CANCELLED
                    elapsed += wait_seconds

                # Send warning (skip if it's the initial duration, already sent)
                if warning_min < duration_minutes:
                    if warning_min == 1:
                        msg = f"**{action_name} in 1 Minute!**"
                    else:
                        msg = f"**{action_name} in {warning_min} Minuten**"
                    await self._send_warning(msg)

            # Final wait (last warning to end)
            remaining = total_seconds - elapsed
            if remaining > 0:
                cancelled = await self._wait_or_cancel(remaining)
                if cancelled:
                    await self._send_cancel_message()
                    return TimerResult.CANCELLED

            # Timer completed
            await self._send_warning(
                f"**Server wird jetzt {action_name.lower()}et...**",
                is_final=True
            )

            if on_complete:
                await on_complete()

            logger.info(f"Timer completed: {action_name}")
            return TimerResult.COMPLETED

        except Exception as e:
            logger.error(f"Timer error: {e}")
            return TimerResult.ERROR

        finally:
            self._active = False
            self._action_name = ""
            self._cancel_event.clear()

    async def _wait_or_cancel(self, seconds: float) -> bool:  # type: ignore
        """Wait for given seconds or until cancelled. Returns True if cancelled."""
        try:
            await asyncio.wait_for(self._cancel_event.wait(), timeout=seconds)
            return True  # Event was set = cancelled
        except asyncio.TimeoutError:
            return False  # Normal timeout = continue

    async def _send_warning(self, message: str, is_initial: bool = False,
                            is_final: bool = False) -> None:  # type: ignore
        """Send warning to both Discord and in-game"""
        # Discord message
        if self.channel:
            try:
                if is_initial:
                    embed = discord.Embed(
                        title=f"⏰ {message}",
                        description="Nutze `/sat cancel` zum Abbrechen.",
                        color=0xe67e22
                    )
                    await self.channel.send(embed=embed)
                elif is_final:
                    await self.channel.send(f"🔄 {message}")
                else:
                    await self.channel.send(f"⏰ {message}")
            except discord.DiscordException as e:
                logger.debug(f"Discord message failed: {e}")

        # In-game message via API
        if self.api:
            try:
                # Strip markdown for in-game
                clean_msg = message.replace("**", "").replace("*", "")
                await self.api.run_command(f"say {clean_msg}")
            except Exception as e:
                logger.debug(f"In-game message failed: {e}")

    async def _send_cancel_message(self) -> None:  # type: ignore
        """Send cancellation message"""
        if self.channel:
            try:
                embed = discord.Embed(
                    title=f"❌ {self._action_name} abgebrochen",
                    color=0x95a5a6
                )
                await self.channel.send(embed=embed)
            except discord.DiscordException as e:
                logger.debug(f"Failed to send cancel embed: {e}")

        if self.api:
            try:
                await self.api.run_command(
                    f"say {self._action_name} abgebrochen!"
                )
            except Exception as e:
                logger.debug(f"Failed to send in-game cancel message: {e}")

        logger.info(f"Timer cancelled: {self._action_name}")


class RestartTimerManager:
    """
    Manages multiple timers (e.g. one per server type).
    Provides cancel-all and status checking.
    """

    def __init__(self) -> None:
        from typing import Dict
        self._timers: Dict[str, RestartTimer] = {}

    def get_or_create(self, key: str, api=None,
                      channel: Optional[discord.TextChannel] = None) -> RestartTimer:  # type: ignore
        """Get existing timer or create new one"""
        if key not in self._timers:
            self._timers[key] = RestartTimer(api=api, channel=channel)
        else:
            # Update channel/api if provided
            timer = self._timers[key]
            if api:
                timer.api = api
            if channel:
                timer.channel = channel
        return self._timers[key]

    def get_active(self) -> Optional[RestartTimer]:
        """Get the currently active timer, if any"""
        for timer in self._timers.values():
            if timer.is_active:
                return timer
        return None

    def cancel_all(self) -> None:  # type: ignore
        """Cancel all active timers"""
        for timer in self._timers.values():
            if timer.is_active:
                timer.cancel()

    @property
    def has_active(self) -> bool:  # type: ignore
        return any(t.is_active for t in self._timers.values())
