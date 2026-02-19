"""
Anti-Spam System
Rate limiting for commands and chat messages per user.
Configurable limits, cooldowns, and auto-mute.
"""

import time
from collections import defaultdict
from typing import Tuple, Optional, Dict, List, Set
from utils.logger import get_logger

logger = get_logger("anti_spam")


class AntiSpam:
    """
    Per-user rate limiting for commands and chat messages.
    
    Features:
    - Configurable message limit per time window
    - Separate limits for commands and chat
    - Auto-cooldown after exceeding limit
    - Duplicate message detection
    - Exempt users (admins/owner)
    """

    def __init__(
        self,
        max_messages: int = 5,
        window_seconds: int = 10,
        cooldown_seconds: int = 30,
        max_duplicate: int = 3,
        command_limit: int = 3,
        command_window: int = 10,
    ):
        """
        Args:
            max_messages: Max messages per window before cooldown
            window_seconds: Time window for message counting
            cooldown_seconds: How long user is cooled down after spam
            max_duplicate: Max identical messages before flag
            command_limit: Max commands per command_window
            command_window: Time window for command counting
        """
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.max_duplicate = max_duplicate
        self.command_limit = command_limit
        self.command_window = command_window

        # Tracking data: user_id -> list of timestamps
        self._messages: Dict[int, List[float]] = defaultdict(list)
        self._commands: Dict[int, List[float]] = defaultdict(list)
        self._cooldowns: Dict[int, float] = {}  # user_id -> cooldown_until
        self._last_messages: Dict[int, List[str]] = defaultdict(list)  # duplicate tracking
        self._exempt_users: Set[int] = set()

        self.enabled = True

    def add_exempt(self, user_id: int) -> None:  # type: ignore
        """Add user to exempt list (admins/owner)"""
        self._exempt_users.add(user_id)

    def remove_exempt(self, user_id: int) -> None:  # type: ignore
        """Remove user from exempt list"""
        self._exempt_users.discard(user_id)

    def check_message(self, user_id: int, content: str = "") -> Tuple[bool, Optional[str]]:
        """
        Check if a chat message should be allowed.
        
        Returns:
            (allowed, reason) - False if message should be blocked
        """
        if not self.enabled or user_id in self._exempt_users:
            return True, None

        now = time.time()

        # Check cooldown
        if user_id in self._cooldowns:
            if now < self._cooldowns[user_id]:
                remaining = int(self._cooldowns[user_id] - now)
                return False, f"Cooldown: {remaining}s verbleibend"
            else:
                del self._cooldowns[user_id]

        # Clean old entries
        self._messages[user_id] = [
            t for t in self._messages[user_id]
            if now - t < self.window_seconds
        ]

        # Check rate limit BEFORE appending
        if len(self._messages[user_id]) >= self.max_messages:
            self._cooldowns[user_id] = now + self.cooldown_seconds
            logger.warning(
                f"Anti-spam triggered for user {user_id}: "
                f"{len(self._messages[user_id])} messages in {self.window_seconds}s"
            )
            return False, f"Zu viele Nachrichten! {self.cooldown_seconds}s Cooldown."

        # Now append the new message
        self._messages[user_id].append(now)

        # Check duplicates
        if content:
            content_lower = content.strip().lower()
            self._last_messages[user_id].append(content_lower)
            # Keep only last N messages
            self._last_messages[user_id] = self._last_messages[user_id][-10:]

            recent = self._last_messages[user_id][-self.max_duplicate:]
            if (len(recent) >= self.max_duplicate and
                    all(m == content_lower for m in recent)):
                self._cooldowns[user_id] = now + self.cooldown_seconds
                return False, "Doppelte Nachrichten erkannt! Cooldown."

        return True, None

    def check_command(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """
        Check if a command should be allowed.
        
        Returns:
            (allowed, reason) - False if command should be blocked
        """
        if not self.enabled or user_id in self._exempt_users:
            return True, None

        now = time.time()

        # Check cooldown
        if user_id in self._cooldowns:
            if now < self._cooldowns[user_id]:
                remaining = int(self._cooldowns[user_id] - now)
                return False, f"Cooldown: {remaining}s verbleibend"
            else:
                del self._cooldowns[user_id]

        # Clean old entries
        self._commands[user_id] = [
            t for t in self._commands[user_id]
            if now - t < self.command_window
        ]

        # Check rate limit BEFORE appending
        if len(self._commands[user_id]) >= self.command_limit:
            self._cooldowns[user_id] = now + self.cooldown_seconds
            logger.warning(
                f"Command spam from user {user_id}: "
                f"{len(self._commands[user_id])} commands in {self.command_window}s"
            )
            return False, f"Zu viele Commands! {self.cooldown_seconds}s Cooldown."

        # Now append the new command
        self._commands[user_id].append(now)
        return True, None

    def reset_user(self, user_id: int) -> None:  # type: ignore
        """Reset all tracking for a user"""
        self._messages.pop(user_id, None)
        self._commands.pop(user_id, None)
        self._cooldowns.pop(user_id, None)
        self._last_messages.pop(user_id, None)

    def is_on_cooldown(self, user_id: int) -> bool:  # type: ignore
        """Check if user is currently on cooldown"""
        if user_id in self._cooldowns:
            if time.time() < self._cooldowns[user_id]:
                return True
            else:
                del self._cooldowns[user_id]
        return False

    def get_cooldown_remaining(self, user_id: int) -> int:  # type: ignore
        """Get remaining cooldown seconds for a user"""
        if user_id in self._cooldowns:
            remaining = self._cooldowns[user_id] - time.time()
            return max(0, int(remaining))
        return 0

    def get_stats(self) -> Dict[str, int]:  # type: ignore
        """Get anti-spam statistics"""
        now = time.time()
        active_cooldowns = sum(
            1 for t in self._cooldowns.values() if t > now
        )
        return {
            "enabled": self.enabled,
            "tracked_users": len(self._messages),
            "active_cooldowns": active_cooldowns,
            "exempt_users": len(self._exempt_users),
        }

    def cleanup(self) -> None:  # type: ignore
        """Remove expired tracking data"""
        now = time.time()

        # Remove expired cooldowns
        expired = [
            uid for uid, until in self._cooldowns.items()
            if until < now
        ]
        for uid in expired:
            del self._cooldowns[uid]

        # Remove old message tracking
        for uid in list(self._messages.keys()):
            self._messages[uid] = [
                t for t in self._messages[uid]
                if now - t < self.window_seconds * 2
            ]
            if not self._messages[uid]:
                del self._messages[uid]

        for uid in list(self._commands.keys()):
            self._commands[uid] = [
                t for t in self._commands[uid]
                if now - t < self.command_window * 2
            ]
            if not self._commands[uid]:
                del self._commands[uid]
