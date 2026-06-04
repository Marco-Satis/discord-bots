"""
Discord Invite Filter — blockt fremde Discord-Einladungslinks (Admin Bot).

Erkennt discord.gg / discord(app).com/invite / discord.me / dsc.gg-Links.
Standardmaessig AUS (konservativ) — per ``/filter invite`` aktivierbar. Eigene
oder erlaubte Codes koennen ueber ``allowed_codes`` ausgenommen werden.

Die reine Erkennung (`find_invites`) ist discord-frei + unit-testbar.

NICHT fuer RCON/In-Game.
"""

import json
import re

import aiofiles
from pathlib import Path

from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR

logger = get_logger("moderation.invite_filter")

# Standard-Persistenzdatei
DEFAULT_INVITE_FILE = ADMIN_DATA_DIR / "discord_invite_filter.json"

# discord.gg/<code>, discord(app).com/invite/<code>, discord.me/<code>,
# dsc.gg/<code> — http(s):// und www. optional, Code 2-32 alphanumerisch/-.
# Linkes (?<![\w.]) verhindert Teil-Treffer wie "notdiscord.gg" / "mydiscord.me".
_INVITE_RE = re.compile(
    r"(?<![\w.])(?:https?://)?(?:www\.)?"
    r"(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me|dsc\.gg)/"
    r"([A-Za-z0-9\-]{2,32})",
    re.IGNORECASE,
)


def find_invites(content: str | None) -> list[str]:
    """Alle Discord-Invite-Codes in einem Text (leere Liste = keine)."""
    if not content:
        return []
    return _INVITE_RE.findall(content)


class InviteFilter:
    """
    Blockt fremde Discord-Einladungslinks. Config-Key ``invite_filter``::

        {
            "invite_filter": {
                "enabled": false,
                "allowed_codes": ["meinserver"]
            }
        }
    """

    def __init__(self, config: dict) -> None:
        inv = config.get("invite_filter", {})
        # Konservativ: standardmaessig deaktiviert (sonst sofortiges Loeschen).
        self.enabled: bool = bool(inv.get("enabled", False))
        self.allowed_codes: set[str] = {
            str(c).lower() for c in inv.get("allowed_codes", [])
        }
        self._filepath: Path = DEFAULT_INVITE_FILE

    def check_message(self, content: str) -> tuple[bool, str | None]:
        """
        Prueft eine Nachricht auf fremde Invite-Links.

        Returns:
            (should_filter, reason) — False wenn deaktiviert, kein Invite,
            oder alle gefundenen Codes auf der Allowlist stehen.
        """
        if not self.enabled:
            return False, None
        codes = find_invites(content)
        if not codes:
            return False, None
        foreign = [c for c in codes if c.lower() not in self.allowed_codes]
        if not foreign:
            return False, None
        return True, "Discord-Einladungslink"

    def toggle(self) -> bool:
        """Filter ein-/ausschalten. Returns neuer Zustand (True = aktiv)."""
        self.enabled = not self.enabled
        logger.info(f"Invite-Filter {'aktiviert' if self.enabled else 'deaktiviert'}")
        return self.enabled

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    async def _save(self) -> None:
        """Aktuellen Zustand in JSON-Datei speichern."""
        data = {
            "enabled": self.enabled,
            "allowed_codes": sorted(self.allowed_codes),
        }
        try:
            self._filepath.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Invite-Filter speichern fehlgeschlagen: {e}")

    async def load(self) -> None:
        """Persistierten Zustand laden (ueberschreibt Config-Defaults)."""
        try:
            if self._filepath.exists():
                async with aiofiles.open(self._filepath, "r", encoding="utf-8") as f:
                    data = json.loads(await f.read())
                self.enabled = bool(data.get("enabled", self.enabled))
                self.allowed_codes = {
                    str(c).lower()
                    for c in data.get("allowed_codes", self.allowed_codes)
                }
                logger.info(f"Invite-Filter geladen (aktiv: {self.enabled})")
        except Exception as e:
            logger.error(f"Invite-Filter laden fehlgeschlagen: {e}")
